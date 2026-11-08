"""Keep tests off the dev database and live Nessie. Without this they read backend/data/zeroday.sqlite3 (approved
plans, applied options) and the sandbox's live balances, so a demo approve in the dev app breaks them."""

import os
import tempfile

os.environ["PAIDWAY_DB"] = os.path.join(tempfile.mkdtemp(prefix="paidway-test-"), "test.sqlite3")
os.environ["NESSIE_KEY"] = ""
