# Public demo deploy (paidway-prod Lightsail VM)

https://paidway.kevin-cooke.com (also https://paidway.3-229-64-25.sslip.io): its own Lightsail instance
`paidway-prod` (us-east-1, static IP 3.229.64.25, no other users). Caddy (TLS) serves `frontend/dist` and proxies
`/api` to uvicorn on 127.0.0.1:8720 (`paidway-api.service`). 80/443 are public; 22 only from homepc.
DNS: Cloudflare A record `paidway` -> 3.229.64.25, DNS-only. SSH: `ssh paidway-prod` (key lives only on homepc).

- **Own Nessie data:** `paidway.env` points the id map and SQLite at `/var/lib/paidway` and seeds customers
  named "Paidway C001/C027", so nothing here touches the "PNC" customers local dev uses. `reseed` is refused.
- **Idle reset:** `paidway-reset.timer` runs `idle_reset.py` each minute. Once the app was used and then sat
  idle 15 min, it restores `baseline.sqlite3` (seeded, no proposals) and re-mirrors C001/C027 in Nessie.
  Force one: `sudo systemctl stop paidway-reset.timer; /opt/paidway/backend/.venv/bin/python /opt/paidway/deploy/idle_reset.py --force`
  with the env file loaded (or `sudo systemctl start paidway-reset.service` after touching the activity file).
- **Rate limits** (`backend/src/paidway/api/guard.py`, on with `PAIDWAY_PUBLIC=1`), per 10 min:
  first runs 6/IP and 40 total, chat edits 20/IP and 120 total, approve/retry 6/IP and 30 total.
- **Update:** `deploy/deploy.sh` (builds the frontend, rsyncs, restarts).
