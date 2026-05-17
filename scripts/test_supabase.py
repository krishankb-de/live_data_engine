"""Smoke test for the Supabase connection.

Run from project root:
    python scripts/test_supabase.py

Verifies:
  1. .env loads SUPABASE_URL + SUPABASE_SECRET_KEY
  2. supabase-py can authenticate
  3. The `recipes` table exists (i.e. schema.sql has been applied)

Does NOT write anything. Pure connectivity + schema presence check.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from scraper.supabase_client import smoke_test


def main() -> int:
    result = smoke_test()
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
