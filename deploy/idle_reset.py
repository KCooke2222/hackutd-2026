"""Idle reset for the public demo (run by paidway-reset.timer every minute).

If anyone used the app since the last reset and it has now been quiet for IDLE_MIN minutes, put it back to the
seeded baseline: stop the API, restore the baseline SQLite, re-mirror C001 and C027 in Nessie (this deployment's
own customers only, via PAIDWAY_NESSIE_IDS / PAIDWAY_NESSIE_TAG), start the API.

    python idle_reset.py            # check and reset if idle
    python idle_reset.py --force    # reset now
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

IDLE_MIN = int(os.getenv("PAIDWAY_IDLE_MIN", "15"))
STATE = Path(os.getenv("PAIDWAY_STATE_DIR", "/var/lib/paidway"))
ACTIVITY = Path(os.getenv("PAIDWAY_ACTIVITY_FILE", STATE / "activity"))
STAMP = STATE / "last-reset"
DB = Path(os.environ["PAIDWAY_DB"])
BASELINE = STATE / "baseline.sqlite3"
CUSTOMERS = ["C001", "C027"]


def due() -> bool:
    if not ACTIVITY.exists():
        return False
    last_use = ACTIVITY.stat().st_mtime
    last_reset = STAMP.stat().st_mtime if STAMP.exists() else 0
    return last_use > last_reset and time.time() - last_use >= IDLE_MIN * 60


def reset() -> None:
    subprocess.run(["sudo", "systemctl", "stop", "paidway-api"], check=True)
    try:
        for suffix in ("-wal", "-shm", "-journal"):
            Path(str(DB) + suffix).unlink(missing_ok=True)
        shutil.copy2(BASELINE, DB)
        from paidway.data import seed

        for cid in CUSTOMERS:
            r = seed.reset_customer(cid)
            print(cid, "re-mirrored:", r.get("deleted_accounts"), "accounts replaced")
    finally:
        subprocess.run(["sudo", "systemctl", "start", "paidway-api"], check=True)
    STAMP.touch()
    print("reset done")


if __name__ == "__main__":
    if "--force" in sys.argv or due():
        reset()
