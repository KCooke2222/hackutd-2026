"""Settings from environment (.env at repo root or backend/)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent

load_dotenv(REPO_DIR / ".env")
load_dotenv(BACKEND_DIR / ".env")

PNC_DATA_DIR = Path(os.getenv("PNC_DATA_DIR", BACKEND_DIR / "data" / "pnc"))
CUSTOMERS_SPEC = Path(os.getenv("CUSTOMERS_SPEC", BACKEND_DIR / "data" / "customers.yaml"))
DB_PATH = Path(os.getenv("ZERODAY_DB", BACKEND_DIR / "data" / "zeroday.sqlite3"))

NESSIE_BASE_URL = os.getenv("NESSIE_BASE_URL", "https://api.nessieisreal.com").rstrip("/")
NESSIE_KEY = os.getenv("NESSIE_KEY", "").strip()

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "").strip()
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
NEMOTRON_MODEL = os.getenv("NEMOTRON_MODEL", "nvidia/nemotron-3.5-super-vl-120b-a12b")
