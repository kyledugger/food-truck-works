@'
import os
import subprocess
import sys
from dotenv import dotenv_values

env_file = ".env.local-prod-db"
url = dotenv_values(env_file).get("DATABASE_URL")
if not url:
    sys.exit(f"DATABASE_URL is missing from {env_file}")

env = {**os.environ, "DATABASE_URL": url, "DOTENV_FILE": env_file}
sys.exit(subprocess.call([
    sys.executable, "backfill_poynt_transactions.py",
    "--organization-id", "1",
    "--start", "2025-12-06",
    "--end", "2025-12-07",
    "--timezone", "America/Phoenix",
], env=env))
'@ | python -