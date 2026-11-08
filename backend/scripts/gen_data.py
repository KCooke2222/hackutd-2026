"""One data generator: customer specs (data/customers.yaml) -> every CSV in data/pnc + history_manifest.json.

    cd backend && uv run python scripts/gen_data.py        # or: make data. Writes data/pnc, loads linked accounts
    uv run python scripts/gen_data.py --check              # regenerate to a temp dir and compare with data/pnc
    uv run python scripts/gen_data.py --init-spec          # rebuild customers.yaml from pnc_original + seeded draws

Each customer in data/customers.yaml says what the household earns, owns and owes (PNC accounts and the
linked ones), its regular bills, where it shops, how card spending splits, its transfers out, and its lumpy
spending (mix, annual bills, which months the trips and repairs land in). The ledger is cent-exact: every
account reconciles month by month and ends at its spec balance.

How it fills in the months:
- PNC's random stream (seed 20261108, same calls in the same order as PNC's generator) gives monthly income
  and total-spending noise, savings/CD/IRA deposits and loan principal. Incomes grow 4% over the history,
  spending is +12% in Nov/Dec.
- A per-customer stream gives Zeroday's choices: merchant picks, per-transaction splits and days, and the
  default for any knob the spec leaves out. A knob's default is drawn even when the spec gives the value,
  so editing one knob doesn't reshuffle the rest of that customer's history.
- Lumpy spending `total: auto` takes whatever the regular lines leave, so checking lands on its balance
  (PNC's opening = 90% of the final balance). Give a dollar `total` (over the history) or `per_month`
  instead and the opening checking balance absorbs the difference.

Derived from `Lab_Package/generate_history.py` in PNC's pnc-utd-hackathon-2026 repository
(https://github.com/devfulcrum/pnc-utd-hackathon-2026), licensed under the Apache License, Version 2.0
(http://www.apache.org/licenses/LICENSE-2.0). Modified for Zeroday; the changes are listed in
backend/data/pnc/README.md ("Changes vs PNC").
"""
import argparse
import calendar
import csv
import json
import random
import sys
import tempfile
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

BACKEND = Path(__file__).resolve().parents[1]
SPEC = BACKEND / 'data' / 'customers.yaml'
OUT = BACKEND / 'data' / 'pnc'
ORIGINAL = BACKEND / 'data' / 'pnc_original'
SEED = 20261108
ASSETS = {'checking', 'savings', 'cd', 'ira'}
YIELDS = {'checking': 0, 'savings': 2.5, 'cd': 4.25, 'ira': 3.5}

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
# Lumpy spending: (default share of the leftover, merchants). Trips, holidays, repairs, annual bills.
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
IRREGULAR_FLOOR = .03      # every household has at least 3% of income in lumpy spending
CARD_CATS = ('groceries', 'transport', 'dining', 'shopping')


def cents(value):
    """Convert a numeric value to integer cents using half-up rounding."""
    return int((Decimal(str(value)) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def dollars(value):
    """Convert integer cents to a fixed 2-decimal USD string."""
    return f'{value / 100:.2f}'


def usd(value):
    """Integer cents -> a float of dollars for the spec (round-trips through cents())."""
    return value / 100


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


class Knobs:
    """A customer's knobs: the spec's value when it has one, else the seeded default (drawn either way).
    `used` collects what was used, which is what --init-spec writes out."""

    def __init__(self, spec):
        self.spec, self.used = spec or {}, {}

    def __call__(self, key, default):
        value = self.spec[key] if key in self.spec else default
        self.used[key] = value
        return value

    def sub(self, key):
        knobs = Knobs(self.spec.get(key))
        self.used[key] = knobs.used
        return knobs


def lumpy_plan(crng, periods, total, has_car, family, knobs):
    """Per month {category: cents} summing exactly to `total`, and the merchant names to use per month."""
    shares = {c: share * crng.uniform(.75, 1.25) for c, (share, _) in IRREGULAR.items()}
    if not has_car:
        shares.pop('car_maintenance')
    if not family:
        shares.pop('family_support')
    bills = crng.sample(IRREGULAR['annual_bills'][1], 2) + (['Texas vehicle registration'] if has_car else [])
    bill_months = crng.sample(range(1, 13), len(bills))
    calendar_default = []  # per 12-month block from the history start: calendar months of the lumpy events
    for _ in range((len(periods) + 11) // 12):
        calendar_default.append(dict(
            summer=crng.choice([6, 7, 8]), trip2=crng.choice([3, 11, 12]), weekend=crng.sample(range(1, 13), 2),
            birthdays=crng.sample(range(1, 13), 3), home_big=crng.sample(range(1, 13), 2),
            car_big=crng.choice(range(1, 13)), oil=crng.randint(1, 4),
            medical=crng.sample(range(1, 13), crng.randint(2, 3)), family=crng.sample(range(1, 13), 2)))
    shares = knobs('mix', shares)
    annual = knobs('annual_bills', [dict(name=b, month=m) for b, m in zip(bills, bill_months)])
    bills, bill_months = [b['name'] for b in annual], [b['month'] for b in annual]
    picks = knobs('calendar', calendar_default)
    norm = sum(shares.values())

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


def num(text):
    """A PNC CSV number as the spec writes it: int if it had no decimal point, else float."""
    return float(text) if '.' in text else int(text)


def spec_from_original(src=ORIGINAL):
    """The PNC part of the spec (income, spending base, credit limit, accounts) from pnc_original."""
    with (src / 'profiles.csv').open(newline='', encoding='utf-8') as stream:
        profiles = list(csv.DictReader(stream))
    with (src / 'accounts.csv').open(newline='', encoding='utf-8') as stream:
        accounts = list(csv.DictReader(stream))
    customers = {}
    for p in profiles:
        cid = p['customer_id']
        owned = {}
        for a in accounts:
            if a['customer_id'] == cid:
                row = {'balance': num(a['balance'])}
                for key in ('monthly_payment', 'apr_pct'):
                    if a[key] != '0':
                        row[key] = num(a[key])
                owned[a['account_type']] = row
        customers[cid] = dict(income=num(p['monthly_income']), spending=num(p['monthly_non_debt_expenses']),
                              credit_limit=num(p['credit_limit']), accounts=owned)
    return customers


def generate(spec, out_dir=OUT, *, seeded_linked=False):
    """Write every CSV in `out_dir` plus the manifest from `spec`. Returns (manifest, knobs used per customer).

    seeded_linked: draw linked accounts with paidway.data.linked's seeded generator instead of the spec's (--init-spec).
    """
    from paidway.data import linked

    out_dir = Path(out_dir)
    history = spec.get('history') or {}
    end = history.get('end', date(2026, 10, 31))
    months = history.get('months', 24)
    seed = history.get('seed', SEED)
    if not 1 <= months <= 120 or end.day != calendar.monthrange(end.year, end.month)[1]:
        raise ValueError('Use 1–120 months and a month-end date')
    end_index = end.year * 12 + end.month - 1
    periods = [date(index // 12, index % 12 + 1, 1)
               for index in range(end_index - months + 1, end_index + 1)]
    start = periods[0]
    rng = random.Random(seed)  # PNC's stream: same calls in the same order as PNC's generator
    transactions, statements, enriched_accounts, new_profiles = [], [], [], []
    used = {}
    event_serial = 0

    for cid, c in spec['customers'].items():
        crng = random.Random(f'{seed}:{cid}')  # Zeroday's per-customer choices; never touches PNC's stream
        knobs = Knobs(c)
        by_type = {}
        for kind, a in c['accounts'].items():
            by_type[kind] = dict(customer_id=cid, account_type=kind, balance=str(a['balance']),
                                 monthly_payment=str(a.get('monthly_payment', 0)), apr_pct=str(a.get('apr_pct', 0)),
                                 account_id=f'A-{cid}-{kind.upper()}')
        if 'checking' not in by_type:
            raise ValueError(f'{cid}: every customer needs a checking account')
        linked_rows = linked.seeded_rows(cid) if seeded_linked else linked.from_spec(cid, c.get('linked') or [])
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

        # ---------- savings / CD / IRA (PNC) ----------
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

        # ---------- loans (PNC) ----------
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

        # ---------- regular bills and spending (PNC's totals, Zeroday's split) ----------
        income_base, expense_base = cents(c['income']), cents(c['spending'])
        has_card = 'credit_card' in by_type
        owner = 'mortgage' in by_type
        linked_mins = [(r['name'], r['institution'], cents(r['min_payment']), r['account_type'])
                       for r in linked_rows if r['account_type'] in linked.DEBT_TYPES and r['balance'] > 0 and r['min_payment']]
        has_brokerage = any(r['account_type'] == 'brokerage' for r in linked_rows)
        has_car = any(r['account_type'] == 'property' and r['kind'] == 'vehicle' for r in linked_rows) or 'auto_loan' in by_type
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
        insurance = ([(f'{car_insurer} auto insurance', car_ins, 7)] if car_ins else []) + \
                    ([(renters_insurer, renters, 7)] if renters else [])

        bills = knobs.sub('bills')

        def lines(key, default):  # (name, cents, day) <-> spec [{name, amount, day}]
            got = bills(key, [dict(name=n, amount=usd(a), day=d) for n, a, d in default])
            return [(x['name'], cents(x['amount']), x['day']) for x in got]
        housing_lines = lines('housing', housing_lines)
        insurance = lines('insurance', insurance)
        phone = lines('phone_internet', phone)
        subs = lines('subscriptions', subs)
        util = bills('utilities', dict(share=utilities_pct, electric=electric, water=WATER[0], gas=GAS_UTILITY[0]))
        utilities_pct = util['share']
        shop_at = knobs('merchants', dict(groceries=grocers, dining=diners, shopping=shops, fuel=gas))
        grocers, diners, shops, gas = shop_at['groceries'], shop_at['dining'], shop_at['shopping'], shop_at['fuel']
        if has_card:
            card_weights = knobs('card_mix', card_weights)

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
            fixed += [('insurance', n, a, d) for n, a, d in insurance]
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
            crng.choice(WATER), crng.choice(GAS_UTILITY)  # PNC-era draws, kept so the stream doesn't shift
            for (name, day), amount in zip([(util['electric'], 12), (util['water'], 16), (util['gas'], 20)],
                                           split(utilities, [.62, .16, .22])):
                add('checking', m, day, 'payment', amount, -1, 'utilities', 'expense', description=name)
            spend('checking', m, 'healthcare', health, PHARMACY, crng.randint(1, 2))
            spend('checking', m, 'groceries', groceries, grocers, crng.randint(2, 4), txtype='withdrawal')
            spend('checking', m, 'other_spending', rest, HOUSEHOLD, crng.randint(2, 4))

            if card_spend:
                cats = list(CARD_CATS)
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

        # ---------- transfers out: linked-loan minimums, auto-invest, childcare, family ----------
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
        transfers = knobs.sub('transfers')
        broker_amt = cents(transfers('brokerage_auto_invest', usd(broker_amt)))
        childcare = transfers('childcare', dict(provider=provider, monthly=usd(child_amt)) if child_amt else None)
        provider, child_amt = (childcare['provider'], cents(childcare['monthly'])) if childcare else (provider, 0)
        support = transfers('family_support', dict(to=family, monthly=usd(fam_amt)) if fam_amt else None)
        family, fam_amt = (support['to'], cents(support['monthly'])) if support else (family, 0)
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

        # ---------- lumpy named spending ----------
        # PNC's monthly checking targets are still drawn (same random stream) but only the final one binds.
        final = cents(by_type['checking']['balance'])
        for m in range(months - 1):
            rng.uniform(.8, 1.2)
        flows = [sum(e['change_cents'] for e in account_events['checking'] if e['posted_date'][:7] == p.strftime('%Y-%m'))
                 for p in periods]
        lumpy = knobs.sub('lumpy')
        how_much = lumpy('total', 'auto')
        if 'per_month' in lumpy.spec:
            irregular_total = months * cents(lumpy('per_month', None))
        elif how_much == 'auto':
            irregular_total = max(int(final * .9) + sum(flows) - final, months * int(income_base * IRREGULAR_FLOOR))
        else:
            irregular_total = cents(how_much)
        opening['checking'] = final - sum(flows) + irregular_total
        if opening['checking'] < 0:
            raise ValueError(f'{cid}: lumpy total too small; checking would have to start below zero')
        plan, names_for = lumpy_plan(crng, periods, irregular_total, has_car, family if fam_amt else None, lumpy)
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
            raise ValueError(f'{cid}: lumpy spending does not land on the final checking balance')
        new_profiles.append(dict(customer_id=cid, monthly_income=str(c['income']), monthly_non_debt_expenses=dollars(
            expense_base + child_amt + fam_amt + irregular_total // months), credit_limit=str(c['credit_limit'])))
        used[cid] = dict(linked=linked.to_spec(linked_rows), **knobs.used)

        # ---------- ledger + statements (PNC) ----------
        for kind, account in by_type.items():
            events = sorted(account_events[kind], key=lambda e: (e['posted_date'], e['serial']))
            # `shift` moves the whole ledger by a flat amount after the draws, so one balance can change
            # without reshuffling the customer's history (C001 savings: drawn at 28k, shown at 24.8k).
            shift = cents((c['accounts'][kind] or {}).get('shift', 0))
            opening[kind] += shift
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
            if balance != cents(account['balance']) + shift:
                raise ValueError(f'Closing balance mismatch: {cid} {kind}')
            if shift:
                account['balance'] = dollars(balance)
            account.update(opening_balance=dollars(opening[kind]), history_start=start.isoformat(),
                           as_of_date=end.isoformat(), annual_yield_pct=YIELDS.get(kind, 0))
            enriched_accounts.append(account)

    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / 'profiles.csv', new_profiles)
    write_csv(out_dir / 'accounts.csv', enriched_accounts)
    write_csv(out_dir / 'transactions.csv', transactions)
    write_csv(out_dir / 'monthly_statements.csv', statements)
    manifest = dict(schema_version=3, seed=seed, history_start=start.isoformat(), as_of_date=end.isoformat(),
        months=months, customers=len(new_profiles), accounts=len(enriched_accounts), transactions=len(transactions),
        monthly_statements=len(statements), currency='USD',
        derived_from='PNC pnc-utd-hackathon-2026 Lab_Package/generate_history.py (Apache-2.0), modified by Zeroday',
        balance_convention='Assets and liabilities are positive; balance_change increases or decreases that account balance.',
        calibration='Opening balances are calibrated to preserve supplied ending balances. What the household does not spend '
                    'on regular lines goes to lumpy, merchant-named irregular spending; nothing leaves to an unknown destination.',
        limitations='Synthetic monthly accrual, no daily compounding, IRA market returns, taxes, contribution eligibility, or actual banking fees modeled.')
    (out_dir / 'history_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest, used


# ---------- the spec file ----------

SPEC_HEADER = '''\
# Customer specs: one entry per household. `make data` (scripts/gen_data.py) turns this file into every CSV in
# data/pnc/ plus history_manifest.json, and loads the linked accounts into SQLite. Money is USD; "monthly"
# amounts are per month. Leave a knob out and the generator draws its seeded default.
#
# history: end (a month-end), months, seed.
# customers.<id>:
#   income          monthly take-home pay (grows 4% over the history, +-2% noise per month)
#   spending        PNC's monthly non-debt spending base (+12% in Nov/Dec, +-6% noise); bills below come out of it
#   credit_limit    total card credit limit (profiles.csv)
#   accounts        PNC accounts by type, with the balance on the as-of date (every account ends there),
#                   plus monthly_payment and apr_pct for debts
#   linked          accounts outside PNC (student loans, store cards, brokerage, home/car value) as if linked
#                   through an aggregator; debt minimums are paid from checking every month
#   bills           fixed monthly lines from checking: housing (rent, or HOA + escrow + repairs), insurance,
#                   phone_internet, subscriptions [{name, amount, day}]; utilities: share of monthly spending
#                   (seasonal: x1.15 Jun-Aug, Dec, Jan) and the three utility names
#   merchants       where the household shops (groceries, dining, shopping, fuel)
#   card_mix        relative weights of card spending (22% of spending when there is a card)
#   transfers       brokerage_auto_invest (monthly), childcare {provider, monthly}, family_support {to, monthly}
#   lumpy           irregular, merchant-named spending (trips, gifts, home, car, annual bills, medical, family)
#     total         `auto` = whatever is left after the lines above, so checking lands on its balance; or a dollar
#                   total over the history; or per_month: <dollars>. The opening checking balance absorbs the rest.
#     mix           relative weights by category
#     annual_bills  [{name, month}] one-off yearly charges
#     calendar      per 12-month block from the history start: month of the summer trip, second trip (trip2),
#                   weekend trips, birthdays, big home buys, a big car repair, oil-change phase (months
#                   where month % 4 == oil % 4), medical bills and family help
'''


class _Dumper(yaml.SafeDumper):
    pass


def _leaf(value):
    return not isinstance(value, (dict, list)) or (isinstance(value, list) and all(not isinstance(v, (dict, list)) for v in value))


def _represent_dict(dumper, data):
    flow = all(_leaf(v) for v in data.values()) and len(data) <= 12
    return dumper.represent_mapping('tag:yaml.org,2002:map', data.items(), flow_style=flow)


def _represent_list(dumper, data):
    return dumper.represent_sequence('tag:yaml.org,2002:seq', data, flow_style=all(_leaf(v) and not isinstance(v, list) for v in data))


_Dumper.add_representer(dict, _represent_dict)
_Dumper.add_representer(list, _represent_list)


def write_spec(spec, path=SPEC):
    body = yaml.dump(spec, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=1000)
    Path(path).write_text(SPEC_HEADER + body, encoding='utf-8')


def load_spec(path=SPEC):
    return yaml.safe_load(Path(path).read_text(encoding='utf-8'))


def init_spec(path=SPEC, src=ORIGINAL, end=date(2026, 10, 31), months=24):
    """Rebuild the spec from pnc_original plus the seeded draws (what the data was first generated from)."""
    base = dict(history=dict(end=end, months=months, seed=SEED), customers=spec_from_original(Path(src)))
    with tempfile.TemporaryDirectory() as tmp:
        _, used = generate(base, tmp, seeded_linked=True)
    for cid, c in base['customers'].items():
        c.update(used[cid])
    write_spec(base, path)
    return base


# ---------- fidelity: per-customer monthly aggregates ----------

def aggregates(data_dir):
    """{(customer, month, metric): dollars} from a data/pnc-style directory. Metrics: income, essentials,
    discretionary, debt_payments (all payments to debts from cash), savings_invest, and the closing balance of
    each account type (balance:<type>)."""
    from paidway.planning.finance import ESSENTIAL_CATEGORIES

    data_dir = Path(data_dir)
    out = defaultdict(float)
    with (data_dir / 'transactions.csv').open(newline='', encoding='utf-8') as f:
        for t in csv.DictReader(f):
            k, ch, cash = (t['customer_id'], t['posted_date'][:7]), float(t['balance_change']), t['account_type'] in {'checking', 'savings'}
            if t['flow_class'] == 'expense' and (cash or t['account_type'] == 'credit_card'):
                out[(*k, 'essentials' if t['category'] in ESSENTIAL_CATEGORIES else 'discretionary')] += abs(ch)
            elif cash and t['flow_class'] == 'income':
                out[(*k, 'income')] += ch
            elif cash and t['category'] == 'debt_payment':
                out[(*k, 'debt_payments')] += -ch
            elif cash and t['category'] == 'investment_contribution':
                out[(*k, 'savings_invest')] += -ch
    with (data_dir / 'monthly_statements.csv').open(newline='', encoding='utf-8') as f:
        for s in csv.DictReader(f):
            out[(s['customer_id'], s['month'], f'balance:{s["account_type"]}')] = float(s['closing_balance'])
    return dict(out)


def ids(data_dir):
    """Customer, account and (customer, category) ids in a data directory."""
    data_dir = Path(data_dir)
    with (data_dir / 'accounts.csv').open(newline='', encoding='utf-8') as f:
        accounts = {(a['customer_id'], a['account_id']) for a in csv.DictReader(f)}
    with (data_dir / 'transactions.csv').open(newline='', encoding='utf-8') as f:
        cats = {(t['customer_id'], t['category']) for t in csv.DictReader(f)}
    return dict(customers={c for c, _ in accounts}, accounts=accounts, categories=cats)


def compare(new_dir, ref_dir, tolerance=.01, floor=50.0):
    """Relative differences of every per-customer monthly aggregate (|new - ref| / max(|ref|, floor dollars)).
    Returns {max_rel, worst, over, n, ids_match}."""
    new, ref = aggregates(new_dir), aggregates(ref_dir)
    diffs = {k: abs(new.get(k, 0) - ref.get(k, 0)) / max(abs(ref.get(k, 0)), floor) for k in set(new) | set(ref)}
    worst = max(diffs, key=diffs.get)
    a, b = ids(new_dir), ids(ref_dir)
    return dict(max_rel=diffs[worst], worst=dict(key=worst, new=new.get(worst), ref=ref.get(worst)),
                over=sorted(k for k, d in diffs.items() if d > tolerance), n=len(diffs),
                ids_match={k: a[k] == b[k] for k in a})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--spec', type=Path, default=SPEC)
    parser.add_argument('--out', type=Path, default=OUT)
    parser.add_argument('--check', action='store_true', help='regenerate into a temp dir and compare with --out')
    parser.add_argument('--init-spec', action='store_true', help='rebuild --spec from pnc_original + seeded draws')
    parser.add_argument('--no-db', action='store_true', help="don't load linked accounts into SQLite")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(BACKEND / 'src'))
    if args.init_spec:
        init_spec(args.spec)
        print(f'wrote {args.spec}')
        return 0
    spec = load_spec(args.spec)
    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            generate(spec, tmp)
            result = compare(tmp, args.out)
            same = all((Path(tmp) / n).read_bytes() == (args.out / n).read_bytes()
                       for n in ('profiles.csv', 'accounts.csv', 'transactions.csv', 'monthly_statements.csv', 'history_manifest.json'))
        print(json.dumps(dict(byte_identical=same, aggregates=result['n'], max_rel_diff=round(result['max_rel'], 6),
                              over_1pct=len(result['over']), worst=result['worst'] if result['max_rel'] else None,
                              ids_match=result['ids_match']), indent=2, default=str))
        return 0 if not result['over'] and all(result['ids_match'].values()) else 1
    manifest, _ = generate(spec, args.out)
    print(json.dumps(manifest, indent=2))
    if not args.no_db and args.spec == SPEC:
        from paidway.data import linked
        print(f'loaded {linked.load()} linked accounts into SQLite')
    return 0


if __name__ == '__main__':
    sys.exit(main())
