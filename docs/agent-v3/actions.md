# What a plan can do, and who does it

Paying off debt comes down to six kinds of moves. Each plan step (`execute.schedule`, field `runs`) is one of:
**Nessie** (`nessie`: a real API call on approve), **agent** (`agent`: watched at each monthly check-in,
with a `watch` line saying what it checks), or **you do it** (`manual`: a step the agent suggests and helps
with; the check-in asks whether it happened). `recorded` is only a fallback when there is no simulation or
no Nessie mirror. One `summary` step carries the month's split (below); it is not a list item.

| # | Kind of move | Example | How it runs |
|---|---|---|---|
| 1 | One-time: cash to debt | Pay the PNC card from savings | **Nessie** transfer + payment, one row per payoff |
| 1b | One-time: sell an asset | Sell brokerage, CD | **You do it** at the broker (no brokerage type in Nessie), then the cash payment is a Nessie transfer |
| 2 | Monthly payment and order | Federal loan $195/mo (minimum); extra above the minimums goes to the target debt | **Nessie** recurring bills. Seeding creates today's payments as bills (below); approve sends only the diff: `PUT /bills/{id}` for a new amount, nothing for "keep", `POST` for a debt with no bill yet. Text says minimum vs. extra |
| 3 | Spending cuts | Travel $719 to $431 | **Agent**: the check-in reads next month's purchases against the target |
| 4 | Redirect savings | Pause auto-invest, 401k above match | IRA / brokerage / CD contributions are **Nessie** bills: pause = `DELETE /bills/{id}` with the spec kept, and the check-in re-creates it (`POST`) in the resume month. A 401(k) match is never paused. Other redirects are **agent**-watched |
| 5 | Stop new debt | Autopay in full once at $0 | Paid off at approve with planned charges: **Nessie** bill for the statement now ("your new purchases, not debt"). Paid off at approve with no planned charges: no bill, **agent** reads the balance. Paid off later: the check-in switches its bill to the statement (`PUT`), or ends it (`DELETE`) when no charges are planned, and moves the freed money to the next debt's bill |
| 6 | Make the debt cheaper | IDR on federal loans, hardship rate, balance transfer, consolidation, mortgage help | **You do it**: each card comes with the steps, where to go and a call script; the solver models the result where it can (IDR, hardship rate) |
| - | Earn more | Extra hours, side income | Stated only, never pre-ticked |

## The month's split (honest numbers)

`monthly_debt_budget` is a ceiling, not what gets paid. From month 1 of the simulation (`wealth.paid_split`):

- `paid_to_debt_per_month`: what the debts take (sum of month-1 payments, statement bills included)
- `new_card_purchases`: the part of that which only covers new card spending
- `net_paydown_per_month` = paid − card spending: what actually shrinks the debt (the headline)
- `unused_per_month` = budget − paid: no debt needs it, so it **stays in cash**. Never call it paydown.
  Where it should go (loan, cash, investing) is an open product decision; the solver leaves it in cash.

Example, C001 after its payoffs: $1,619 paid ($195 Federal minimum + $1,423 PNC statement),
$1,423 of it is new purchases, debt shrinks $196/mo, $759/mo stays in cash.

## Recurring in Nessie (the baseline)

`planning/recurring.baseline` lists today's recurring outflows; `data/seed.seed_bills` creates one recurring
bill on the checking mirror per row (debt payments, IRA / brokerage / CD contributions, housing, utilities,
phone and internet, insurance, subscriptions). Variable spending stays card purchases. The bill map lives in
the Nessie id file under `bills` and `paused`; the Snapshot shows it ("Recurring in Nessie",
`GET /api/customers/{cid}/nessie-bills`).

A plan is a diff against it (`execute.bill_diff`): keep / change / pause / end / new. Approve also records a
`bill_schedule` from the simulation: month-by-month bill changes (a debt hits $0, a pause ends). Each monthly
check-in (`POST .../tracking/step`, and a check-in with a scenario) runs the entries that are due
(`execute.apply_due_bills`), once each, and logs them under `execution.bill_log`.

## Nessie, what it has

Accounts: Checking, Savings, Credit Card. Also: transfers, deposits, withdrawals, purchases, bills
(one-time or recurring), loans, merchants. No brokerage, CD or IRA, so those stay linked accounts.

## Not built yet

- Off-track check-ins don't re-plan the bills; the schedule runs as approved until a new plan is approved.
