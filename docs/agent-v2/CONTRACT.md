# Agent v2 contract (frozen for the parallel build)

Goal: maximize 24-month net worth; all debt above 8% APR to $0 as fast as that allows.
Safety: the agent can only READ and PROPOSE. No tool writes anything except `propose_plan`
(a pending proposal row). Money moves only on human approve, after re-validation.
`propose_plan` does the arithmetic: it fills the debt budget to `budget_limit`, and when the plan leaves
PNC cash above the buffer idle next to high-cost debt, it adds those lump sums (fitted to
keep the buffer, `validator.missing_lumps`) if that raises 24-month net worth.

## Ownership (no fork edits another fork's files)

| Fork | Owns |
|---|---|
| A engine | `assumptions.py`, `wealth.py`, `agent/validator.py`, `tests/test_wealth.py` |
| B data | `insights.py`, `agent/sql_tool.py`, `api_insights.py` (APIRouter, one include line in `api.py`), `frontend/src/components/IncomeExpense.tsx` (+1 line in `pages/Home.tsx`), `tests/test_sql_tool.py`, `tests/test_insights.py` |
| C agent | `agent/graph.py`, `agent/tools.py`, `agent/prompts.py`, `agent/execute.py`, agent/simulate/proposal routes in `api.py`, `frontend/src/components/flow/*`, `frontend/src/lib/flow.ts`, `frontend/src/lib/api.ts` |
| main | `scripts/eval_agent.py`, `tests/test_reference_plan.py`, integration, commits |

Nobody commits, stashes or switches branches. Main commits at the end.

## assumptions.py (A)

```python
ASSUMPTIONS = {
  "horizon_months": 24,
  "prepay_hurdle_pct": 8.0,          # debts above this get extra money
  "default_emergency_months": 3.0,
  "min_emergency_months": 2.0,       # validator floor
  "returns_pct": {"cash": None, "cd": 4.8, "brokerage": 7.0, "ira": 7.0},  # cash None = account yield
  "cd_penalty_months_interest": 3,
  "ltcg_pct": 15.0, "stcg_pct": 22.0,
  "ira_penalty_pct": 10.0, "income_tax_pct": 22.0,
  "max_cut_pct": 30.0,               # per discretionary category, unless cancelling a recurring charge
}
```

## Plan v2 (dict)

```python
{
  "version": 2,
  "monthly_debt_budget": float,     # total paid to ALL debts per month, minimums included
  "extra_order": [account_id, ...], # extra money order (highest APR first); only debts > hurdle
  "lump_sums": [{"source": "cash"|"cd"|"brokerage", "amount": float, "to_debt": account_id}],
  "redirects": [{"flow": "monthly_surplus"|"brokerage_auto_invest"|"cd_contribution", "amount_per_month": float}],
  "spending_targets": [{"category": str, "current": float, "target": float,
                        "proof_month": "YYYY-MM", "how": [str]}],
  "autopay_full_cards": [account_id],
  "keep_emergency_months": float,
}
```
Budget sources: `monthly_debt_budget <= current monthly debt payments + sum(redirects) + sum(current - target)`.

Flex flags (`zeroday/categories.py`, enforced by the validator): every spending category and named flow is
`fixed` (never moved), `ask` (never moved; a question for the customer), `trim` (≤ its max %), `cancel`
(only by cancelling named subscriptions), `redirect` (the monthly surplus left in checking, only when the
residual is positive) or `pause` (brokerage auto-invest, CD contributions). Redirected surplus would otherwise
stay in cash, so the simulation only credits the APR-minus-cash-yield spread. The history's lumpy spending is
named: travel, gifts_holidays, home_household are `trim` (40/40/30%); car_maintenance, annual_bills are
`fixed`; healthcare and family_support are `ask`. Brokerage / CD sales are `use=false` in compare_sources when
cash above the buffer plus the surplus and pauses (`wealth.flexible_monthly`; cuts don't count) clears the debt within
12 months, since selling then loses net worth.

Cuts are the customer's choice: the validator rejects invalid cuts (fixed/ask items, over the limit, below the
lowest month, no proof month) but never requires one, and the agent proposes none unless asked. After
propose_plan the server adds `simulation.cut_options` (`wealth.cut_options`): the 3 trim/cancel cuts that buy the
most 24-month net worth, each priced alone on top of the plan (`cut_per_month`, `merchants`,
`high_cost_debt_free`, `months_sooner`, `net_worth_24m_gain`, `interest_saved`, and a ready `spending_target`).
`explain.options` words the top 2-3 as tradeoffs. The eval grades against a no-cuts reference.

## wealth.py (A)

```python
def compare_sources(customer_id: str, amount: float, target_debt: str) -> dict
# {"target": {"account_id","name","apr_pct","balance"},
#  "sources": [{"source": "surplus"|"cash"|"cd"|"brokerage"|"ira", "available", "gross_needed",
#               "upfront_cost", "nw_delta_24m", "use": bool, "why": str}]}   # sorted best first

def simulate_plan(customer_id: str, plan: dict | None) -> dict   # plan None = baseline (current behavior)
# {"feasible": bool, "problems": [str],
#  "debts": [{"account_id","name","apr_pct","balance","payoff_month": "YYYY-MM"|None,"interest"}],
#  "months": [{"month","total_debt","high_cost_debt","cash","invested","net_worth"}],   # horizon+1 rows
#  "net_worth_24m", "baseline_net_worth_24m", "interest_total", "baseline_interest_total",
#  "high_cost_debt_free": "YYYY-MM"|None, "upfront_cost"}
```

## agent/validator.py (A)

```python
class PlanRejected(ValueError): reasons: list[str]
def validate_plan_v2(customer_id: str, plan: dict) -> dict   # returns simulate_plan(...) or raises
```
Rejects: IRA/property source; draw > tier available; cash below `min_emergency_months`;
budget over its sources; any debt below its minimum; essentials cut; cut > max_cut_pct
(unless `how` cancels a recurring charge); target below the customer's own lowest month;
missing proof_month or merchant in `how`; extra_order missing a debt above hurdle, or
including one below it; lump/extra into debt below hurdle; infeasible simulation.

## insights.py (B)

```python
def cash_flow(customer_id: str, months: int = 12) -> dict
# {"months": [{"month","income","essentials","discretionary","debt_payments","savings_invest",
#              "surplus"}],
#  "monthly_avg": {same keys}, "saved_per_month": float,
#  "named_flows": [{"flow","per_month","flag","label"}],
#  "spending_by_category": [{"category","monthly_avg","flag","max_cut_pct"?}],
#  "essentials_by_category": {cat: avg}, "card_spending_by_category": {cat: avg}}

def spending_detail(customer_id: str, category: str, months: int = 12) -> dict
# {"category","essential": bool,"avg","p25","min","min_month","monthly":[{"month","amount"}],
#  "merchants":[{"name","monthly_avg","count_per_month"}],"recurring":[{"name","amount","day"}]}

def find_recurring(customer_id: str) -> list[dict]   # [{"name","amount","cadence","account","category"}]
```

## agent/sql_tool.py (B)

```python
SCHEMA_DOC: str   # view names + columns, for the prompt
def run_sql(customer_id: str, query: str, max_rows: int = 200) -> dict
# {"columns": [...], "rows": [[...]], "truncated": bool} | {"error": str}
```
In-memory SQLite holding ONLY this customer's rows; views `v_accounts`, `v_transactions`,
`v_debts`, `v_assets`, `v_monthly_cashflow`. Single statement, SELECT/WITH only,
`set_authorizer` allows read/select/function only, `PRAGMA query_only=ON`,
progress-handler timeout, row cap. Never touches the app DB file.

## Reference plan (eval oracle, `scripts/eval_agent.py`)

Built in code from real money only, for any customer, and it must pass the same validator:
- extra_order: debts above the hurdle by APR (C001: store card 27.24% → PNC card 22.5% → personal loan 11%).
- Lump sums from PNC cash above the emergency buffer when compare_sources says use,
  shrunk until the simulation keeps the buffer (a household with a negative residual can't spend its cushion).
- If high-cost debt remains: redirect the monthly surplus (if any) and pause flexible contributions.
- No spending cuts (the customer's choice; graded softly: the explanation must offer the top cut options).
- Autopay full on cards; keep 3 months emergency.
- C001: no cash above the buffer and no surplus. This reference only pauses $225/mo brokerage (not free within
  24 months, ≈ +$1.1k over baseline); the solver's no-cuts plan sells ~$6.9k brokerage instead (free 2027-10,
  $34,764). TODO: use the solver's plan as the eval reference. Optional cuts: travel (Southwest, Marriott)
  $287/mo → free 2028-01, +$8.0k; other (Amazon, CVS) $256/mo → +$7.2k; home (Lowe's, Ashley) $165/mo → +$4.7k.
