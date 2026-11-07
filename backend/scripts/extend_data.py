"""Extend PNC's data with linked external accounts and load them into SQLite.

    cd backend && uv run python scripts/extend_data.py        # or: make linked

Deterministic: same rows every run. Generation lives in zeroday/linked.py.
"""

from zeroday import linked

if __name__ == "__main__":
    print(f"loaded {linked.load()} linked accounts into SQLite")
