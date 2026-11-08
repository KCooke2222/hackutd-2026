"""Keep tests off the dev database and live Nessie. Without this they read backend/data/zeroday.sqlite3 (approved
plans, applied options) and the sandbox's live balances, so a demo approve in the dev app breaks them."""

import os
import tempfile

os.environ["PAIDWAY_DB"] = os.path.join(tempfile.mkdtemp(prefix="paidway-test-"), "test.sqlite3")
os.environ["NESSIE_KEY"] = ""

# Nessie ids (seed.IDS_PATH): tests read a copy of the real file and write only to their own copy. Approve and
# bill tests used to write fake bills and ids into backend/data/nessie_ids.json.
import shutil  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

_REAL_IDS = Path(__file__).resolve().parents[1] / "data" / "nessie_ids.json"
_IDS_COPY = Path(tempfile.mkdtemp(prefix="paidway-ids-")) / "nessie_ids.json"
if _REAL_IDS.exists():
    shutil.copyfile(_REAL_IDS, _IDS_COPY)
os.environ["PAIDWAY_NESSIE_IDS"] = str(_IDS_COPY)


@pytest.fixture(autouse=True)
def _own_nessie_ids(tmp_path_factory, monkeypatch):
    from paidway.data import seed

    p = tmp_path_factory.mktemp("ids") / "nessie_ids.json"
    if _IDS_COPY.exists():
        shutil.copyfile(_IDS_COPY, p)
    monkeypatch.setattr(seed, "IDS_PATH", p)
