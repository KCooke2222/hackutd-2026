"""Deterministic, cent-exact synthetic ledger anchored to PNC's closing balances, with named cash flows.

Derived from `Lab_Package/generate_history.py` in PNC's pnc-utd-hackathon-2026 repository
(https://github.com/devfulcrum/pnc-utd-hackathon-2026), licensed under the Apache License, Version 2.0
(http://www.apache.org/licenses/LICENSE-2.0). Modified for Zeroday; the changes are listed in
backend/data/pnc/README.md ("Changes vs PNC"). Summary:

- PNC's residual `external_cash_management` plug (money to "an unlisted external account") is gone.
  The same money is now named lines: linked-loan minimums, brokerage auto-invest, childcare, family
  support (Zelle), and lumpy irregular spending that takes the leftover: travel, gifts and holidays,
  home and household, car maintenance, annual bills, medical bills, extra family help. Every dollar is
  spent at a named merchant; nothing is saved to an account the data doesn't show.
- Direct spending is split realistically (utilities ~5-7%, car insurance, phone + internet,
  subscriptions, healthcare), card spending gets per-month noise instead of four equal pieces, and
  every category has several merchant-named transactions per month.
- Same seed, same PNC random stream: income, total spending, asset and loan flows and checking
  targets are PNC's. Every final balance, scheduled payment and APR is preserved.

    cd backend && uv run python scripts/gen_history.py     # reads data/pnc_original, writes data/pnc
"""
import argparse
import calendar
import csv
import json
import random
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

SEED = 20261108
ASSETS = {'checking', 'savings', 'cd', 'ira'}
YIELDS = {'checking': 0, 'savings': 2.5, 'cd': 4.25, 'ira': 3.5}
BACKEND = Path(__file__).resolve().parents[1]

# ---- merchant lists (deterministic picks per customer) ----
GROCERS = ['Kroger', 'H-E-B', 'Tom Thumb', 'Aldi', 'Trader Joe\'s', 'Sprouts Farmers Market', 'Costco', 'Walmart Grocery']
GAS = ['Shell', 'Exxon', 'QuikTrip', 'Chevron', 'RaceTrac', '7-Eleven Fuel']
RIDES = ['Uber', 'Lyft', 'DART transit pass', 'NTTA toll tag', 'SpotHero parking']
DINING = ['Chipotle', 'Starbucks', 'DoorDash', 'Uber Eats', 'Torchy\'s Tacos', 'Panera Bread', 'Chick-fil-A',
          'Olive Garden', 'Raising Cane\'s', 'Local pizzeria', 'Whataburger', 'Thai Basil']
SHOPPING = ['Amazon', 'Target', 'Best Buy', 'Old Navy', 'TJ Maxx', 'Nike', 'IKEA', 'Etsy', 'Sephora']
HOUSEHOLD = ['Walmart', 'Target', 'Home Depot', 'Lowe\'s', 'CVS household', 'Dollar General', 'Amazon household']
ELECTRIC = ['TXU Energy', 'Reliant Energy', 'Oncor delivery', 'Green Mountain Energy']
WATER = ['City water utilities']
GAS_UTILITY = ['Atmos Energy']
CAR_INSURERS = ['GEICO', 'Progressive', 'State Farm', 'Allstate']
RENTERS_INSURERS = ['Lemonade renters insurance', 'State Farm renters insurance']
MOBILE = [('Verizon Wireless', 75, 145), ('T-Mobile', 60, 130), ('AT&T Wireless', 65, 140), ('Mint Mobile', 25, 45)]
INTERNET = [('Xfinity Internet', 55, 95), ('Spectrum Internet', 50, 90), ('AT&T Fiber', 55, 80)]
SUBSCRIPTIONS = [('Netflix', 15.49), ('Spotify', 11.99), ('Hulu', 9.99), ('Disney+', 13.99), ('Max', 16.99),
                 ('YouTube Premium', 13.99), ('Amazon Prime', 14.99), ('Apple iCloud+', 2.99),
                 ('Planet Fitness', 24.99), ('Peloton App', 12.99), ('Xbox Game Pass', 16.99), ('Audible', 14.95)]
PHARMACY = ['CVS Pharmacy', 'Walgreens', 'Baylor Scott & White copay', 'Texas Health copay', 'Dental copay']
APARTMENTS = ['Greystar - The Monarch Apartments', 'Camden Apartments', 'Lincoln Property - Parkside Lofts',
              'MAA - Riverside Apartments']
HOAS = ['Willow Creek HOA', 'Stonebridge HOA', 'Lakeview HOA']
CHILDCARE = ['Bright Horizons', 'KinderCare', 'The Goddard School', 'Primrose School']
FAMILY = ['Zelle to Mom', 'Zelle to Dad', 'Zelle to sister', 'Zelle to brother', 'Zelle to grandparents']
# Irregular spending: (share of the leftover, merchants). Lumpy by design: trips, holidays, repairs, annual bills.
IRREGULAR = {
    'travel': (.26, ['Southwest Airlines', 'American Airlines', 'Delta Air Lines', 'Marriott', 'Hilton', 'Airbnb',
                     'Expedia', 'Enterprise Rent-A-Car']),
    'gifts_holidays': (.14, ['Amazon gifts', 'Target', 'Macy\'s', 'Etsy', 'Kohl\'s', 'Apple Store', 'Party City']),
    'home_household': (.22, ['Home Depot', 'Lowe\'s', 'IKEA', 'Wayfair', 'Costco', 'Ashley Furniture']),
    'car_maintenance': (.12, ['Jiffy Lube', 'Firestone Complete Auto Care', 'Discount Tire', 'Pep Boys',
                              'Toyota service center']),
    'annual_bills': (.10, ['Costco annual membership', 'Term life insurance annual premium', 'AAA membership',
                           'H&R Block tax preparation']),
    'healthcare': (.08, ['Smile Dental', 'CareNow Urgent Care', 'Baylor Scott & White hospital bill', 'LensCrafters',
                         'Quest Diagnostics']),
    'family_support': (.08, []),
}
IRREGULAR_DAYS = (16, 28)  # after the second paycheck
IRREGULAR_FLOOR = .03      # every household has at least 3% of income in irregular spending


def cents(value):
    """Convert a numeric value to integer cents using half-up rounding."""
    return int((Decimal(str(value)) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def dollars(value):
    """Convert integer cents to a fixed 2-decimal USD string."""
    return f'{value / 100:.2f}'


def interest(balance, annual_pct):
    """Simple monthly accrual: balance * annual_pct / 12 with cent rounding."""
    return int((Decimal(balance) * Decimal(str(annual_pct)) / 1200).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def write_csv(path, rows):
    """Write a list of dictionaries to CSV with stable column ordering."""
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def split(total, weights):
    """Split integer cents by weights; the last piece takes the rounding so the pieces sum exactly."""
    s = sum(weights)
    pieces = [int(total * w / s) for w in weights[:-1]]
    return pieces + [total - sum(pieces)]


def round_to(value, step):
    return int(round(value / step) * step)


def linked_profile(cid):
    """Linked debts' minimums and whether there is a linked brokerage / owned car (from zeroday.linked)."""
    from zeroday import linked

    rows = linked.base_rows(cid)
    mins = [(r['name'], r['institution'], cents(r['min_payment']), r['account_type'])
            for r in rows if r['account_type'] in linked.DEBT_TYPES and r['balance'] > 0 and r['min_payment']]
    car = any(r['account_type'] == 'property' and r['kind'] == 'vehicle' for r in rows)
    brokerage = any(r['account_type'] == 'brokerage' for r in rows)
    return mins, brokerage, car


def irregular_plan(crng, periods, total, has_car, family):
    """Per month {category: cents} summing exactly to `total`, and the merchant names to use per month."""
    shares = {c: share * crng.uniform(.75, 1.25) for c, (share, _) in IRREGULAR.items()}
    if not has_car:
        shares.pop('car_maintenance')
    if not family:
        shares.pop('family_support')
    norm = sum(shares.values())
    bills = crng.sample(IRREGULAR['annual_bills'][1], 2) + (['Texas vehicle registration'] if has_car else [])
    bill_months = crng.sample(range(1, 13), len(bills))
    picks = {}  # per year block: calendar months for the lumpy events
    for y in range((len(periods) + 11) // 12):
        picks[y] = dict(summer=crng.choice([6, 7, 8]), trip2=crng.choice([3, 11, 12]),
                        weekend=crng.sample(range(1, 13), 2), birthdays=crng.sample(range(1, 13), 3),
                        home_big=crng.sample(range(1, 13), 2), car_big=crng.choice(range(1, 13)),
                        oil=crng.randint(1, 4), medical=crng.sample(range(1, 13), crng.randint(2, 3)),
                        family=crng.sample(range(1, 13), 2))

    def weight(cat, m):
        cal, p = periods[m].month, picks[m // 12]
        if cat == 'travel':
            return (1.0 if cal == p['summer'] else 0) + (.7 if cal == p['trip2'] else 0) + (.15 if cal in p['weekend'] else 0)
        if cat == 'gifts_holidays':
            return {11: .3, 12: 1.0}.get(cal, 0) + (.12 if cal in p['birthdays'] else 0)
        if cat == 'home_household':
            return .25 + (1.2 if cal in p['home_big'] else 0)
        if cat == 'car_maintenance':
            return (.15 if cal % 4 == p['oil'] % 4 else 0) + (1.0 if cal == p['car_big'] else 0)
        if cat == 'annual_bills':
            return sum(w for bm, w in zip(bill_months, (1.0, .6, .4)) if bm == cal)
        if cat == 'healthcare':
            return .7 if cal in p['medical'] else 0
        return (.6 if cal in p['family'] else 0) + (.1 if cal == 12 else 0)

    plan = [dict() for _ in periods]
    names = [dict() for _ in periods]
    cats = sorted(shares)
    left = total
    for k, cat in enumerate(cats):
        cat_total = left if k == len(cats) - 1 else int(total * shares[cat] / norm)
        left -= cat_total
        w = [weight(cat, m) for m in range(len(periods))]
        if not sum(w):
            w = [1.0] * len(periods)
        amounts = [int(cat_total * x / sum(w)) for x in w]
        amounts[max(range(len(w)), key=lambda m: w[m])] += cat_total - sum(amounts)
        for m, amount in enumerate(amounts):
            if amount > 0:
                plan[m][cat] = amount
                if cat == 'annual_bills':
                    names[m][cat] = [b for b, bm in zip(bills, bill_months) if bm == periods[m].month] or bills
                elif cat == 'family_support':
                    names[m][cat] = [family]
    return plan, names


def generate_history(src_dir=None, out_dir=None, end=date(2026, 10, 31), months=24):
    """Generate deterministic ledger history anchored to snapshot closing balances."""
    src_dir = Path(src_dir or BACKEND / 'data' / 'pnc_original')
    out_dir = Path(out_dir or BACKEND / 'data' / 'pnc')
    if not 1 <= months <= 120 or end.day != calendar.monthrange(end.year, end.month)[1]:
        raise ValueError('Use 1–120 months and a month-end date')
    with (src_dir / 'profiles.csv').open(newline='', encoding='utf-8') as stream:
        profiles = list(csv.DictReader(stream))
    with (src_dir / 'accounts.csv').open(newline='', encoding='utf-8') as stream:
        accounts = [{k: a[k] for k in ('customer_id', 'account_type', 'balance', 'monthly_payment', 'apr_pct')}
                    for a in csv.DictReader(stream)]
    end_index = end.year * 12 + end.month - 1
    periods = [date(index // 12, index % 12 + 1, 1)
               for index in range(end_index - months + 1, end_index + 1)]
    start = periods[0]
    rng = random.Random(SEED)  # PNC's stream: same calls in the same order as PNC's generator
    transactions, statements, enriched_accounts, new_profiles = [], [], [], []
    event_serial = 0

    for profile in profiles:
        cid = profile['customer_id']
        crng = random.Random(f'{SEED}:{cid}')  # Zeroday's per-customer choices; never touches PNC's stream
        owned = [dict(a) for a in accounts if a['customer_id'] == cid]
        by_type = {a['account_type']: a for a in owned}
        if len(by_type) != len(owned) or 'checking' not in by_type:
            raise ValueError('History generator expects one row per customer/type and a checking account')
        for kind, account in by_type.items():
            account['account_id'] = f'A-{cid}-{kind.upper()}'
        account_events = {kind: [] for kind in by_type}
        opening = {}
        savings_payroll = [0] * months
        savings_expenses = [0] * months

        def add(kind, month_index, day, transaction_type, amount, sign, category,
                flow_class, transfer_id='', description=None):
            # sign=+1 increases the account balance; sign=-1 decreases it.
            nonlocal event_serial
            if amount <= 0:
                return
            event_serial += 1
            period = periods[month_index]
            account_events[kind].append(dict(
                customer_id=cid, account_id=by_type[kind]['account_id'], account_type=kind,
                posted_date=period.replace(day=min(day, calendar.monthrange(period.year, period.month)[1])).isoformat(),
                transaction_type=transaction_type, category=category,
                description=description or transaction_type.replace('_', ' ').capitalize(),
                amount_cents=amount, change_cents=sign * amount, flow_class=flow_class,
                transfer_id=transfer_id, serial=event_serial))

        def paired(kind, month_index, day, transaction_type, amount, sign, category):
            # Create both sides of an internal transfer with a shared transfer_id.
            link = f'X-{cid}-{kind}-{month_index:02d}-{transaction_type}'
            add(kind, month_index, day, transaction_type, amount, sign, category, 'internal_transfer', link)
            checking_type = ('transfer_out' if sign > 0 else 'deposit') if kind in ASSETS else 'payment'
            add('checking', month_index, day, checking_type,
                amount, -sign if kind in ASSETS else -1, category, 'internal_transfer', link,
                f'Synthetic linked {kind.replace("_", " ")} {transaction_type.replace("_", " ")}')

        def spend(kind, m, category, total, merchants, count, days=(2, 27), txtype=None):
            # Several merchant-named transactions that sum exactly to `total` cents.
            if total <= 0:
                return
            count = max(1, min(count, total // 500 or 1))
            names = [crng.choice(merchants) for _ in range(count)]
            for name, amount in zip(names, split(total, [crng.uniform(.5, 1.5) for _ in range(count)])):
                ttype = txtype or ('purchase' if kind == 'credit_card' else 'payment')
                add(kind, m, crng.randint(*days), ttype, amount, 1 if kind == 'credit_card' else -1, category,
                    'expense', description=name)

        # ---------- PNC: savings / CD / IRA (unchanged) ----------
        for kind in ['savings', 'cd', 'ira']:
            if kind not in by_type:
                continue
            final = cents(by_type[kind]['balance'])
            deposits = [max(1, int(final * rng.uniform(.0015, .004))) for _ in periods]
            withdrawals = [int(final * .001) if kind == 'savings' and m % 3 == 0 else 0 for m in range(months)]

            def ending(initial):
                value = initial
                for deposit, withdrawal in zip(deposits, withdrawals):
                    value += deposit - withdrawal
                    value += interest(value, YIELDS[kind])
                return value
            low, high = 0, final
            while low < high:
                mid = (low + high + 1) // 2
                if ending(mid) <= final:
                    low = mid
                else:
                    high = mid - 1
            opening[kind] = value = low
            for m in range(months):
                deposit = deposits[m]
                if kind == 'savings':
                    savings_payroll[m] = deposit // 2
                    add(kind, m, 1, 'paycheck', savings_payroll[m], 1, 'payroll', 'income',
                        description='Split-payroll deposit to savings')
                    deposit -= savings_payroll[m]
                paired(kind, m, 5, 'deposit', deposit, 1, 'savings_contribution' if kind == 'savings' else 'investment_contribution')
                value += deposits[m]
                if withdrawals[m]:
                    if kind == 'savings' and m % 6 == 0:
                        savings_expenses[m] = withdrawals[m]
                        add(kind, m, 22, 'payment', withdrawals[m], -1, 'other_spending', 'expense',
                            description=crng.choice(HOUSEHOLD))
                    else:
                        paired(kind, m, 22, 'withdrawal', withdrawals[m], -1, 'savings_withdrawal')
                    value -= withdrawals[m]
                accrued = interest(value, YIELDS[kind])
                add(kind, m, 31, 'interest_accrual', accrued, 1, 'asset_interest', 'non_cash')
                value += accrued
                if m == months - 1 and final > value:
                    paired(kind, m, 31, 'deposit_rounding', final - value, 1, 'investment_contribution')
                    value = final

        # ---------- PNC: loans (unchanged) ----------
        for kind in ['auto_loan', 'personal_loan', 'mortgage']:
            if kind not in by_type:
                continue
            account = by_type[kind]
            final = cents(account['balance'])
            principal = [max(1, int(cents(account['monthly_payment']) * rng.uniform(.18, .32))) for _ in periods]
            opening[kind] = value = final + sum(principal)
            for m in range(months):
                accrued = interest(value, account['apr_pct'])
                fee = 500 if m % 7 == 3 else 0
                add(kind, m, 20, 'interest_accrual', accrued, 1, 'debt_interest', 'non_cash')
                add(kind, m, 20, 'fee', fee, 1, 'servicing_fee', 'non_cash')
                paired(kind, m, 21, 'payment', principal[m] + accrued + fee, -1, 'debt_payment')
                value -= principal[m]

        # ---------- spending: PNC's totals, realistic split (Zeroday) ----------
        income_base, expense_base = cents(profile['monthly_income']), cents(profile['monthly_non_debt_expenses'])
        has_card = 'credit_card' in by_type
        owner = 'mortgage' in by_type
        linked_mins, has_brokerage, has_car = linked_profile(cid)
        has_car = has_car or 'auto_loan' in by_type
        direct_base = int(expense_base * .98 * (.78 if has_card else 1.0))
        housing = round_to(direct_base * crng.uniform(.40, .46), 500)
        if owner:
            housing_lines = list(zip([crng.choice(HOAS), 'Property tax & insurance escrow', crng.choice(HOUSEHOLD[2:4])],
                                     split(housing, [.15, .6, .25]), [6, 1, 17]))
        else:
            housing_lines = [(crng.choice(APARTMENTS), housing, 1)]
        utilities_pct = crng.uniform(.05, .07)
        car_ins = round_to(crng.uniform(110, 210) * 100, 1) if has_car else 0
        renters = 0 if owner else round_to(crng.uniform(12, 22) * 100, 1)
        mobile = crng.choice(MOBILE)
        internet = crng.choice(INTERNET)
        phone = [(mobile[0], cents(round(crng.uniform(mobile[1], mobile[2]), 2)), 9),
                 (internet[0], cents(round(crng.uniform(internet[1], internet[2]), 2)), 12)]
        subs = [(n, cents(p), crng.randint(2, 27)) for n, p in crng.sample(SUBSCRIPTIONS, crng.randint(2, 5))]
        car_insurer, renters_insurer = crng.choice(CAR_INSURERS), crng.choice(RENTERS_INSURERS)
        electric = crng.choice(ELECTRIC)
        grocers = crng.sample(GROCERS, 3)
        diners = crng.sample(DINING, 6)
        shops = crng.sample(SHOPPING, 4)
        gas = crng.sample(GAS, 2)
        card_weights = {'groceries': crng.uniform(.2, .3), 'transport': crng.uniform(.15, .25),
                        'dining': crng.uniform(.22, .35), 'shopping': crng.uniform(.18, .3)}

        if has_card:
            opening['credit_card'] = int(cents(by_type['credit_card']['balance']) * .98)
        card_value = opening.get('credit_card', 0)
        for m in range(months):
            factor = .96 + .04 * (m / max(months - 1, 1))
            income = int(income_base * factor * rng.uniform(.98, 1.02))
            seasonal = 1.12 if periods[m].month in {11, 12} else 1.0
            spending = int(expense_base * factor * seasonal * rng.uniform(.94, 1.06))
            checking_income = income - savings_payroll[m]
            add('checking', m, 1, 'paycheck', checking_income // 2, 1, 'payroll', 'income', description='Payroll - employer direct deposit')
            add('checking', m, 15, 'paycheck', checking_income - checking_income // 2, 1, 'payroll', 'income', description='Payroll - employer direct deposit')
            card_spend = int(spending * .22) if has_card else 0
            direct = spending - card_spend - savings_expenses[m]

            # Fixed bills first, then variable categories; other_spending takes the remainder.
            fixed = [('housing', n, a, d) for n, a, d in housing_lines]
            if car_ins:
                fixed.append(('insurance', f'{car_insurer} auto insurance', car_ins, 7))
            if renters:
                fixed.append(('insurance', renters_insurer, renters, 7))
            fixed += [('phone_internet', n, a, d) for n, a, d in phone]
            fixed += [('subscriptions', n, a, d) for n, a, d in subs]
            hot = periods[m].month in {6, 7, 8, 12, 1}
            utilities = int(spending * utilities_pct * crng.uniform(.9, 1.1) * (1.15 if hot else .95))
            health = int(spending * crng.uniform(.015, .035))
            groceries = int(direct * crng.uniform(.13, .17))
            rest = direct - sum(a for _, _, a, _ in fixed) - utilities - health - groceries
            if rest < direct * .02:
                raise ValueError(f'{cid} {periods[m]}: direct spending too small for the fixed bills')
            for cat, name, amount, day in fixed:
                add('checking', m, day, 'payment', amount, -1, cat, 'expense', description=name)
            for (name, day), amount in zip([(electric, 12), (crng.choice(WATER), 16), (crng.choice(GAS_UTILITY), 20)],
                                           split(utilities, [.62, .16, .22])):
                add('checking', m, day, 'payment', amount, -1, 'utilities', 'expense', description=name)
            spend('checking', m, 'healthcare', health, PHARMACY, crng.randint(1, 2))
            spend('checking', m, 'groceries', groceries, grocers, crng.randint(2, 4), txtype='withdrawal')
            spend('checking', m, 'other_spending', rest, HOUSEHOLD, crng.randint(2, 4))

            if card_spend:
                cats = list(card_weights)
                pieces = split(card_spend, [card_weights[c] * crng.uniform(.75, 1.25) for c in cats])
                for cat, amount in zip(cats, pieces):
                    names, count = {'groceries': (grocers, crng.randint(3, 5)), 'transport': (gas + RIDES[:2], crng.randint(2, 4)),
                                    'dining': (diners, crng.randint(4, 8)), 'shopping': (shops, crng.randint(2, 5))}[cat]
                    spend('credit_card', m, cat, amount, names, count, days=(1, 25))
                accrued = interest(card_value, by_type['credit_card']['apr_pct'])
                fee = 1500 if m % 9 == 2 else 0
                add('credit_card', m, 26, 'interest_accrual', accrued, 1, 'debt_interest', 'non_cash')
                add('credit_card', m, 26, 'fee', fee, 1, 'card_fee', 'non_cash')
                final = cents(by_type['credit_card']['balance'])
                target = final if m == months - 1 else int(final * (.98 + .02 * (m + 1) / months))
                payment = card_value + card_spend + accrued + fee - target
                if payment <= 0:
                    raise ValueError('Generated card payment must be positive')
                paired('credit_card', m, 28, 'payment', payment, -1, 'debt_payment')
                card_value = target

        # ---------- named monthly lines out of the residual (Zeroday) ----------
        # What's left in checking each month before the irregular spending, averaged.
        left = [sum(e['change_cents'] for e in account_events['checking'] if e['posted_date'][:7] == p.strftime('%Y-%m'))
                for p in periods]
        avg_left = sum(left) / months
        room = avg_left - sum(a for _, _, a, _ in linked_mins)
        broker_amt = round_to(min(max(crng.uniform(.08, .15) * room, 10000), 60000), 2500)
        broker_amt = broker_amt if has_brokerage and room > 60000 else 0
        child_roll, child_amt = crng.random(), round_to(crng.uniform(650, 1400) * 100, 500)
        child_amt = child_amt if child_roll < .3 and room - broker_amt > child_amt + 90000 else 0
        fam_roll, fam_amt = crng.random(), round_to(crng.uniform(100, 400) * 100, 2500)
        fam_amt = fam_amt if fam_roll < .45 and room - broker_amt - child_amt > fam_amt + 60000 else 0
        provider, family = crng.choice(CHILDCARE), crng.choice(FAMILY)
        for m in range(months):
            for name, inst, amount, atype in linked_mins:
                what = 'student loan' if atype == 'student_loan' else name.lower()
                add('checking', m, 14 if atype == 'student_loan' else 22, 'payment', amount, -1, 'debt_payment',
                    'external_transfer', description=f'Payment to {inst} ({what})')
            if broker_amt:
                add('checking', m, 3, 'transfer_out', broker_amt, -1, 'investment_contribution', 'external_transfer',
                    description='Auto-invest to linked brokerage')
            if child_amt:
                add('checking', m, 1, 'payment', child_amt // 2, -1, 'childcare', 'expense', description=provider)
                add('checking', m, 15, 'payment', child_amt - child_amt // 2, -1, 'childcare', 'expense', description=provider)
            if fam_amt:
                add('checking', m, 20, 'payment', fam_amt, -1, 'family_support', 'expense', description=family)

        # ---------- irregular named spending takes the leftover (Zeroday) ----------
        # PNC's monthly checking targets are still drawn (same random stream) but only the final one binds:
        # everything the household doesn't spend on the lines above goes to lumpy, merchant-named spending
        # (travel, gifts, home, car, annual bills, medical, family). Nothing is saved to an unseen account.
        final = cents(by_type['checking']['balance'])
        for m in range(months - 1):
            rng.uniform(.8, 1.2)
        flows = [sum(e['change_cents'] for e in account_events['checking'] if e['posted_date'][:7] == p.strftime('%Y-%m'))
                 for p in periods]
        floor_total = months * int(income_base * IRREGULAR_FLOOR)
        irregular_total = max(int(final * .9) + sum(flows) - final, floor_total)
        opening['checking'] = final - sum(flows) + irregular_total
        plan, names_for = irregular_plan(crng, periods, irregular_total, has_car, family if fam_amt else None)
        value = opening['checking']
        carry = defaultdict(int)
        for m, period in enumerate(periods):
            events = sorted([e for e in account_events['checking'] if e['posted_date'].startswith(period.strftime('%Y-%m'))],
                            key=lambda e: (e['posted_date'], e['serial']))
            running, late_min = value, None
            for event in events:
                running += event['change_cents']
                if int(event['posted_date'][8:]) >= IRREGULAR_DAYS[0]:
                    late_min = running if late_min is None else min(late_min, running)
            late_min = running if late_min is None else min(late_min, running)
            want = {c: plan[m].get(c, 0) + carry[c] for c in set(plan[m]) | set(carry)}
            room = max(late_min - (final if m == months - 1 else max(10000, final // 3)), 0)
            scale = 1.0 if sum(want.values()) <= room or m == months - 1 else room / sum(want.values())
            spent = 0
            for cat in sorted(want):
                amount = want[cat] if m == months - 1 else int(want[cat] * scale)
                carry[cat] = want[cat] - amount
                spent += amount
                names = names_for[m].get(cat) or ([family] if cat == 'family_support' else IRREGULAR[cat][1])
                count = max(1, min(3, amount // 15000))
                spend('checking', m, cat, amount, names, count, days=IRREGULAR_DAYS)
            value = running - spent
        if value != final:
            raise ValueError(f'{cid}: irregular spending does not land on the final checking balance')
        new_profiles.append(dict(profile, monthly_non_debt_expenses=dollars(
            expense_base + child_amt + fam_amt + irregular_total // months)))

        # ---------- ledger + statements (PNC, unchanged) ----------
        for kind, account in by_type.items():
            events = sorted(account_events[kind], key=lambda e: (e['posted_date'], e['serial']))
            balance = opening[kind]
            for m, period in enumerate(periods):
                month_events = [e for e in events if e['posted_date'].startswith(period.strftime('%Y-%m'))]
                initial = balance
                increases = decreases = 0
                for event in month_events:
                    balance += event['change_cents']
                    if balance < 0:
                        raise ValueError(f'Negative balance: {cid} {kind} {event}')
                    increases += max(0, event['change_cents'])
                    decreases += max(0, -event['change_cents'])
                    transactions.append(dict(transaction_id=f'T{len(transactions)+1:07d}',
                        **{key: event[key] for key in ['customer_id', 'account_id', 'account_type', 'posted_date',
                           'transaction_type', 'category', 'description']}, amount=dollars(event['amount_cents']),
                        balance_change=dollars(event['change_cents']), balance_after=dollars(balance),
                        flow_class=event['flow_class'], transfer_id=event['transfer_id']))
                statements.append(dict(customer_id=cid, account_id=account['account_id'], account_type=kind,
                    month=period.strftime('%Y-%m'), opening_balance=dollars(initial),
                    balance_increases=dollars(increases), balance_decreases=dollars(decreases),
                    closing_balance=dollars(balance), transaction_count=len(month_events)))
            if balance != cents(account['balance']):
                raise ValueError(f'Closing balance mismatch: {cid} {kind}')
            account.update(opening_balance=dollars(opening[kind]), history_start=start.isoformat(),
                           as_of_date=end.isoformat(), annual_yield_pct=YIELDS.get(kind, 0))
            enriched_accounts.append(account)

    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / 'profiles.csv', new_profiles)
    write_csv(out_dir / 'accounts.csv', enriched_accounts)
    write_csv(out_dir / 'transactions.csv', transactions)
    write_csv(out_dir / 'monthly_statements.csv', statements)
    manifest = dict(schema_version=3, seed=SEED, history_start=start.isoformat(), as_of_date=end.isoformat(),
        months=months, customers=len(profiles), accounts=len(enriched_accounts), transactions=len(transactions),
        monthly_statements=len(statements), currency='USD',
        derived_from='PNC pnc-utd-hackathon-2026 Lab_Package/generate_history.py (Apache-2.0), modified by Zeroday',
        balance_convention='Assets and liabilities are positive; balance_change increases or decreases that account balance.',
        calibration='Opening balances are calibrated to preserve supplied ending balances. What the household does not spend '
                    'on regular lines goes to lumpy, merchant-named irregular spending; nothing leaves to an unknown destination.',
        limitations='Synthetic monthly accrual, no daily compounding, IRA market returns, taxes, contribution eligibility, or actual banking fees modeled.')
    (out_dir / 'history_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--end', type=date.fromisoformat, default=date(2026, 10, 31))
    parser.add_argument('--months', type=int, default=24)
    parser.add_argument('--src', type=Path, default=None)
    parser.add_argument('--out', type=Path, default=None)
    args = parser.parse_args()
    print(json.dumps(generate_history(args.src, args.out, end=args.end, months=args.months), indent=2))
