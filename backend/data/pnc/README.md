# Synthetic account history

The supplied snapshot contains 30 synthetic customers and 196 accounts. Every account has 24 complete calendar months of generated history, **2024-11-01 through 2026-10-31**. The ledger has **42,701 entries** and **4,704 monthly statements**. `history_manifest.json` records the authoritative dates, seed, counts, and schema version. No real financial records are used.

## Files and keys

| File | Row grain | Key |
| --- | --- | --- |
| `profiles.csv` | One household's snapshot income/expense/limit assumptions | `customer_id` |
| `accounts.csv` | One listed account; original closing balance and scheduled payment preserved | `account_id` |
| `transactions.csv` | One posted ledger entry, including each side of internal transfers | `transaction_id` |
| `monthly_statements.csv` | One account/calendar month | `account_id`, `month` |

Accounts additionally carry `opening_balance`, `history_start`, `as_of_date`, and `annual_yield_pct`. The latter is a synthetic asset-accrual input; `apr_pct` remains the debt-rate input. CDs and IRAs are excluded from the financial tool's liquid-assets measure.

## Transaction fields

| Field | Meaning |
| --- | --- |
| `transaction_id` | Stable unique ledger ID; resolves posting order within a day |
| `customer_id`, `account_id`, `account_type` | Owner and account references |
| `posted_date` | Synthetic ISO posting date |
| `transaction_type` | Paycheck, deposit, withdrawal, payment, purchase, transfer out, fee, interest accrual, or rounding contribution |
| `category`, `description` | Generated purpose and explanation |
| `amount` | Positive USD amount, serialized to two decimal places |
| `balance_change` | Signed increase/decrease in this account's balance |
| `balance_after` | Running balance after the entry |
| `flow_class` | `income`, `expense`, `internal_transfer`, `external_transfer`, or `non_cash` |
| `transfer_id` | Shared ID on exactly two matching internal-transfer entries; otherwise empty |

Sort by `posted_date`, then `transaction_id` to follow an account's ledger. Assets and liabilities are both stored as positive balances. Deposits increase assets; withdrawals decrease assets. Purchases, interest, and fees increase liabilities; debt payments decrease liabilities. A checking-to-loan payment decreases **both** balances. For economic reconciliation, invert the sign of liability movements before adding them to asset movements.

## Generated events

| Type | Activity |
| --- | --- |
| Checking | Two paycheck installments, merchant-named bills and spending, linked savings/investment/debt transfers, payments on linked (other-bank) loans, and lumpy irregular spending (travel, gifts, home, car, annual bills, medical, family) |
| Savings | Split-payroll deposits, linked contributions and withdrawals, occasional direct payments, monthly interest |
| CD | Monthly contributions and synthetic interest accrual |
| IRA | Monthly contributions and simplified interest accrual; no market-price returns or tax/eligibility rules |
| Credit card | Categorized purchases, monthly interest, occasional fees, and paired payments |
| Auto/personal loan/mortgage | Monthly interest, occasional servicing fees, and principal-plus-interest-plus-fee payments paired to checking |

Interest uses a simplified monthly annual-rate/12 calculation with cent rounding. Loan principal reduction is generated independently from the existing snapshot payment assumption; actual historical payments can differ from `accounts.monthly_payment`. Some final asset contributions can include a cent rounding residual. Payroll is split between checking and savings without double-counting income.

## Reconciliation and summary definitions

For each account and month:

```text
opening balance + balance increases − balance decreases = closing balance
next month's opening balance = previous month's closing balance
last month's closing balance = accounts.csv balance
```

Every recorded running balance also reconciles entry by entry. Assets remain nonnegative. Internal-transfer legs have the same owner, date, and amount. The generator preserves all original snapshot balances, scheduled payments, debt APRs and incomes; `monthly_non_debt_expenses` adds the new childcare and family-support lines where a household has them.

Monthly `paycheck_income` sums paychecks across deposit accounts. `spending` counts direct checking/savings expenses and card purchases once. `debt_payments` includes only the liability-side payment, not its checking counterpart. It is shown separately because treating payments as new spending would double-count card purchases and principal movement. Interest, fees, and asset accrual are separate measures. `income_less_spending_and_charges` is paycheck income less spending, debt interest, and fees; it is an accrual activity measure, **not** a checking cash-flow statement or measured savings rate.

Opening balances and the irregular spending are calibrated to the original final snapshot: every account still ends at PNC's closing balance. The checking leftover PNC sent to an unlisted account is spent instead, on merchant-named lumpy categories (a summer trip, holiday gifts, a furniture month, car repairs, registration and premiums, medical bills, money to family), capped so checking never runs dry. Nothing is saved to an account the app can't see. The history is a generated illustration, not a reconstruction of real or previously observed activity. Seasonal spending and fixed yields/fees are generator assumptions.

## Changes vs PNC

`backend/scripts/gen_data.py` is derived from PNC's `Lab_Package/generate_history.py`
([pnc-utd-hackathon-2026](https://github.com/devfulcrum/pnc-utd-hackathon-2026)), licensed under the
[Apache License 2.0](http://www.apache.org/licenses/LICENSE-2.0). PNC's original files are kept unchanged in
`backend/data/pnc_original/`. Same seed (20261108) and the same PNC random stream, so incomes, total
spending, asset/loan flows and final checking balances match PNC; Zeroday's choices use a separate per-customer stream.

| PNC | Zeroday |
| --- | --- |
| A residual `external_cash_management` withdrawal (~$2.9k/mo for C001) to "an unlisted external account", and matching funding deposits | Named lines: payments on linked loans (student loan, store card; amounts match `paidway.data.linked`), brokerage auto-invest (only with a linked brokerage), childcare and Zelle to family (some households), then lumpy irregular spending that takes the leftover: travel 26%, home and household 22%, gifts and holidays 14%, car repairs 12%, annual bills 10%, medical 8%, family support 8% (merchant-named, deterministic per customer) |
| Nothing records where the residual went | It is real spending, so it is in `monthly_non_debt_expenses` (profiles.csv) and the emergency buffer; there is no hidden savings balance |
| Direct spending split .45/.20/.15/rest into housing/utilities/groceries/other | Rent or HOA + escrow + repairs (~40–46%), utilities ~5–7% (seasonal), car and renters insurance, phone + internet, 2–5 subscriptions, healthcare, groceries, other |
| Card spend in four equal pieces | Per-household weights × monthly noise across groceries/transport/dining/shopping |
| One generic description per category | Several transactions per category per month with merchant names from fixed lists |
| CRLF line endings | LF |

## Reproduce

Everything here is generated from `backend/data/customers.yaml`, one entry per customer:

| Spec key | What it drives |
| --- | --- |
| `income`, `spending`, `credit_limit` | PNC's monthly income and non-debt spending base (income grows 4% over the history, ±2% noise; spending +12% in Nov/Dec, ±6%); profiles.csv |
| `accounts` | PNC accounts and their as-of balances, payments and APRs; every account ends exactly there |
| `linked` | Student loans, store cards, brokerage, home/car value (`paidway.data.linked`); debt minimums are paid from checking monthly |
| `bills` | Housing, insurance, phone/internet, subscriptions (name, amount, day); utilities share of spending and names |
| `merchants`, `card_mix` | Where the household shops; how card spending (22% of spending) splits |
| `transfers` | Brokerage auto-invest, childcare, Zelle to family |
| `lumpy` | Irregular spending: `total` (`auto` = the leftover, or dollars, or `per_month`), category `mix`, `annual_bills`, and the `calendar` of trip/repair/medical months per year |

A knob the spec leaves out gets its seeded default. Each knob's default is drawn even when the spec gives
the value, so editing one knob doesn't reshuffle that customer's other transactions; customers never share
Zeroday's random stream. `customers.yaml` itself was written by `--init-spec` (pnc_original + the seeded
draws), and regenerating from it reproduces the previous data byte for byte.

```bash
make data                                                 # = cd backend && uv run python scripts/gen_data.py
cd backend && uv run python scripts/gen_data.py --check   # regenerate to a temp dir; compare monthly aggregates and ids
uv run python scripts/gen_data.py --init-spec             # rebuild customers.yaml from pnc_original + seeded draws
uv run pytest -q tests/test_gen_data.py                   # reconciliation, snapshot, spec == CSVs
```

PNC's original instructions (for `pnc_original/`):

From the package directory:

```bash
# Extend existing snapshots; deterministically replaces history files and account metadata.
python generate_history.py

# Explicitly regenerate the entire original snapshot plus its history.
python generate_data.py

python -m unittest discover -p 'test_*.py'
```

`generate_history.py --end YYYY-MM-DD --months 24` accepts a month-end date. The supplied notebooks and fixture tests target the bundled November 2024–October 2026 period. Restart long-running apps or rerun notebook cells after regeneration. Never put real account data in these files.
