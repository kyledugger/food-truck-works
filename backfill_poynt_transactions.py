"""Pull Poynt transaction history through FTW's configured connection."""
import argparse
import asyncio
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from database import SessionLocal
from poynt.client import PoyntClient, PoyntSignalAPIError
from poynt.connection import get_poynt_credentials
from poynt.transaction_history import backfill_day


async def run(args):
    credentials = get_poynt_credentials(args.organization_id)
    if not credentials:
        raise SystemExit("No Poynt connection for this organization.")
    client = PoyntClient(credentials, args.organization_id)
    zone = ZoneInfo(args.timezone)
    day = args.start
    while day < args.end:
        try:
            count = await backfill_day(client, SessionLocal, args.organization_id, day, zone)
        except PoyntSignalAPIError as exc:
            raise SystemExit(
                f"{day}: Poynt HTTP {exc.status_code}. "
                f"Retry from this date after at least {exc.retry_after} seconds."
            ) from None
        print(f"{day} ({zone.key}): {count} transactions retrieved", flush=True)
        day += timedelta(days=1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", type=int, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True, help="Exclusive local date")
    parser.add_argument("--timezone", required=True, help="IANA reporting timezone")
    args = parser.parse_args()
    if args.start >= args.end:
        parser.error("--end must be later than --start")
    asyncio.run(run(args))
