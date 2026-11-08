# Synthetic account history

The supplied snapshot contains 30 synthetic customers and 196 accounts. Every account has 24 complete calendar months of generated history, **2024-11-01 through 2026-10-31**. The ledger has **21,225 entries** and **4,704 monthly statements**. `history_manifest.json` records the authoritative dates, seed, counts, and schema version. No real financial records are used.

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
| Checking | Two paycheck installments, external funding/deposits, withdrawals, bills, and linked savings/investment/debt transfers |
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

Every recorded running balance also reconciles entry by entry. Assets remain nonnegative. Internal-transfer legs have the same owner, date, and amount. The generator preserves all original snapshot balances, scheduled payments, debt APRs, and profile values.

Monthly `paycheck_income` sums paychecks across deposit accounts. `spending` counts direct checking/savings expenses and card purchases once. `debt_payments` includes only the liability-side payment, not its checking counterpart. It is shown separately because treating payments as new spending would double-count card purchases and principal movement. Interest, fees, and asset accrual are separate measures. `income_less_spending_and_charges` is paycheck income less spending, debt interest, and fees; it is an accrual activity measure, **not** a checking cash-flow statement or measured savings rate.

Opening balances and explicit `external_cash_management` inflows/outflows are calibrated to the original final snapshot. These represent unlisted external accounts. They are shown in separate external-transfer columns and excluded from earned income and spending. They are artificial calibration flows, not evidence of actual household saving behavior. The history is a generated illustration, not a reconstruction of real or previously observed activity. Seasonal spending and fixed yields/fees are generator assumptions.

## Reproduce

From the package directory:

```bash
# Extend existing snapshots; deterministically replaces history files and account metadata.
python generate_history.py

# Explicitly regenerate the entire original snapshot plus its history.
python generate_data.py

python -m unittest discover -p 'test_*.py'
```

`generate_history.py --end YYYY-MM-DD --months 24` accepts a month-end date. The supplied notebooks and fixture tests target the bundled November 2024–October 2026 period. Restart long-running apps or rerun notebook cells after regeneration. Never put real account data in these files.
