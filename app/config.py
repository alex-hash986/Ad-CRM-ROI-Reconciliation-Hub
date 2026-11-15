import os
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DATA = ROOT / "sample_data"
FRONTEND = ROOT / "frontend"
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./reconciliation.db")
DEFAULT_TARGET_ROAS = Decimal("3")
DEFAULT_MAX_CPL = Decimal("1500")
DEFAULT_TARGET_DRR = Decimal("35")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
