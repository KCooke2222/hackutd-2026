# What a plan can do, and who does it

Paying off debt comes down to six kinds of moves. Each plan step is one of three:
**Nessie** (a real API call on approve), **scheduled** (recorded, the app tracks it), or
**you do it** (a manual step the agent suggests and helps with; the check-in asks whether it happened).

| # | Kind of move | Example | How it runs |
|---|---|---|---|
| 1 | One-time: cash to debt | Pay the PNC card from savings | **Nessie** transfer, savings to credit card (exists) |
| 1b | One-time: sell an asset | Sell brokerage, CD | **You do it** at the broker (no brokerage type in Nessie), then the cash payment is a Nessie transfer |
| 2 | Monthly payment and order | $2,385/mo, highest APR first | **Nessie** recurring bills on checking, one per debt (to build) |
| 3 | Spending cuts | Travel $719 to $431 | **Scheduled**: the check-in reads next month's Nessie purchases against the target |
| 4 | Redirect savings | Pause auto-invest, 401k above match | **You do it** with the employer or broker; the check-in confirms |
| 5 | Stop new debt | Autopay in full once at $0 | **Nessie** recurring bill for the statement balance (to build) |
| 6 | Make the debt cheaper | IDR on federal loans, hardship rate, balance transfer, consolidation, mortgage help | **You do it**: each card comes with the steps, where to go and a call script; the solver models the result where it can (IDR, hardship rate) |
| - | Earn more | Extra hours, side income | Stated only, never pre-ticked |

## Nessie, what it has

Accounts: Checking, Savings, Credit Card. Also: transfers, deposits, withdrawals, purchases, bills
(one-time or recurring), loans, merchants. No brokerage, CD or IRA, so those stay linked accounts.

## To make approve do something visible

1. Prefer cash over selling investments when the cushion allows, so the payoff is a real Nessie transfer.
2. Create the monthly payments as recurring Nessie bills from checking.
3. Create the autopay-in-full bill once a card hits $0.
4. "You do it" steps show as a checklist on the plan; the check-in ticks them off or asks.
