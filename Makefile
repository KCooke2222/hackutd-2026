UV ?= uv

.PHONY: install backend frontend test seed reseed linked data record reset-local

install:
	cd backend && $(UV) sync
	cd frontend && npm install

backend:
	cd backend && $(UV) run uvicorn paidway.api.main:app --host 127.0.0.1 --port 8710 --reload --reload-dir src

frontend:
	cd frontend && npm run dev

test:
	cd backend && $(UV) run pytest -q

# Add the demo customers to Nessie (skips ones already seeded). No-op without NESSIE_KEY.
seed:
	cd backend && $(UV) run python -m paidway.data.seed

# DELETE all Nessie data for this key, seed C001-C003 again, rewrite the id map.
reseed:
	cd backend && $(UV) run python -m paidway.data.seed reseed

# Regenerate every CSV in backend/data/pnc from backend/data/customers.yaml and load the linked accounts.
data:
	cd backend && $(UV) run python scripts/gen_data.py

# Reload just the linked external accounts (student loans, brokerage, property, other cards) into SQLite.
linked:
	cd backend && $(UV) run python -m paidway.data.linked

# Record live agent runs as replay fixtures (backend/data/replays/). Re-run after the data or agent changes.
CUSTOMER ?= C001
RUNS ?= 1
record:
	cd backend && $(UV) run python scripts/record_runs.py --customer $(CUSTOMER) --runs $(RUNS) --replace

# Clear a customer's local proposals and demo tracking (SQLite only; never touches Nessie). Demo examples by default.
reset-local:
	cd backend && $(UV) run python scripts/reset_local.py $(if $(filter command line environment,$(origin CUSTOMER)),$(CUSTOMER),C001 C027)
