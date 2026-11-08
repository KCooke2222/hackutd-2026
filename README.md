# Zeroday

A proactive credit-card payoff agent for the PNC challenge (HackUTD 2026). It reads a customer's
accounts, explains why the card balance isn't going down (new spending + 22.5% interest refill
every payment), and proposes one plan to reach $0. **The agent can't move money.** It only saves
a pending proposal; a human approves, edits or rejects it, and only the approve endpoint can call
Nessie's write API.

- `backend/`: FastAPI (uv). PNC CSVs in `backend/data/pnc/`, Nessie client (`nessie/read.py`,
  `nessie/write.py`), LangGraph ReAct agent on Nemotron (`agent/`), SQLite for proposals.
- `frontend/`: React + Vite + TS. `/` = agent check + proposal card, `/data` = PNC data explorer.

## Teammate setup

```bash
git clone https://github.com/KCooke2222/hackutd-2026.git zeroday && cd zeroday
cp .env.example .env
```

Fill in `.env`:

- `NESSIE_KEY`: Nessie data is per key, so each key is its own sandbox.
  - **Dev:** use your own key (http://api.nessieisreal.com, sign in with GitHub) and run
    `make reseed` once. Your seeds won't touch anyone else's.
  - **Demo:** one shared demo key, used only on the demo machine. Don't reseed it from your laptop.
- `NVIDIA_API_KEY`: your own key from https://build.nvidia.com (OpenAI-compatible API).
- `NEMOTRON_MODEL`: leave the default unless we switch models.

Then (needs [uv](https://docs.astral.sh/uv/) and Node 20+):

```bash
make install
make backend     # terminal 1: http://127.0.0.1:8710/api/health
make frontend    # terminal 2: http://127.0.0.1:5710  (proxies /api to the backend)
make test
```

Without keys everything still runs on the local PNC CSVs: the explorer and charts work,
`/api/agent/run` returns 503 "NVIDIA_API_KEY is not set", and approvals are recorded locally only.

## Nessie seed

```bash
make seed        # add C001-C003 to Nessie (skips ones already seeded)
make reseed      # wipe this key's accounts, seed again, rewrite the id map
make linked      # (re)load linked external accounts into SQLite (seed/reseed do this too)
```

Nessie assigns new ids on every seed; the PNC-id → Nessie-id map lives in
`backend/data/nessie_ids.json` (gitignored, per key). `api.nessieisreal.com` has no
`DELETE /data` and no customer delete, so reseed deletes accounts one by one and reuses the
empty `PNC <id>` customers.

## API

| Method | Path | |
|---|---|---|
| GET | `/api/health` | which keys are set |
| GET | `/api/overview` | dataset counts, account types, categories, card-vs-savings per customer |
| GET | `/api/customers`, `/api/customers/{id}` | profile, accounts, totals, monthly statements |
| GET | `/api/customers/{id}/transactions` | filters: account_id, transaction_type, category, flow_class, date_from, date_to; page, page_size |
| GET | `/api/customers/{id}/summary`, `/refill` | card history + why the balance is stuck |
| POST | `/api/simulate` | payoff simulator |
| POST | `/api/agent/run` | `{customer_id}` → agent trace + pending proposal |
| GET | `/api/proposals` | |
| POST | `/api/proposals/{id}/approve` | optional edited plan in the body |
| POST | `/api/proposals/{id}/reject` | |

## Safety

- Agent tools: `get_accounts`, `get_card_activity`, `refill_breakdown`, `simulate_payoff`,
  `propose_plan`. None of them import `nessie.write` (checked by `tests/test_safety.py`).
- Code does the math, the LLM explains. `agent/validator.py` recomputes every number and rejects
  plans that drain savings below 3 months of expenses.
- Approve is idempotent: it atomically flips `pending → executing` in SQLite, so a double click
  gets 409 instead of a second transfer.
- Approve re-reads the card and savings balances (PNC and Nessie). If they changed since the
  proposal, it is marked `stale` and you re-run the check.

## Balance sheet: PNC + Nessie + linked accounts

Nessie only has Checking / Savings / Credit Card accounts (plus home/auto/small-business loans), and
PNC's data has no student loans, brokerage or property. Those are **linked external accounts**:
deterministic synthetic rows (`zeroday/linked.py`, `backend/scripts/extend_data.py`) stored in the
SQLite `linked_accounts` table. `balance_sheet.py` merges PNC (with live Nessie balances where seeded)
and linked accounts, then computes:

- **Liquidity ladder**: 1 cash above the emergency buffer (free), 2 CDs (~6 months' interest),
  3 brokerage (15% tax on the gain), 4 IRA (flag only), 5 property equity (mention only).
  Plans may draw from tiers 1-3; the validator checks each draw and its one-time cost.
- **Debt priority**: every minimum, ordered by consequence (secured, federal student loan,
  unsecured), then extra money to the highest APR. Secured or <8% APR debt gets minimums only.

API: `/api/customers/{id}/balance-sheet`, `/ladder?amount=`, `/debts`, `/api/linked`.
CD and brokerage draws can't run in Nessie; approval records them as manual steps.
