UV ?= uv

.PHONY: install backend frontend test seed reseed

install:
	cd backend && $(UV) sync
	cd frontend && npm install

backend:
	cd backend && $(UV) run uvicorn zeroday.api:app --host 127.0.0.1 --port 8710 --reload --reload-dir src

frontend:
	cd frontend && npm run dev

test:
	cd backend && $(UV) run pytest -q

# Add the demo customers to Nessie (skips ones already seeded). No-op without NESSIE_KEY.
seed:
	cd backend && $(UV) run python -m zeroday.seed

# DELETE all Nessie data for this key, seed C001-C003 again, rewrite the id map.
reseed:
	cd backend && $(UV) run python -m zeroday.seed reseed
