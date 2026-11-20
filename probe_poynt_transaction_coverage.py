"""Compare Poynt's full-day and hourly transaction lists without importing rows."""
import asyncio
import os
from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from dotenv import dotenv_values


async def scan(client, start, end):
    from live_dashboard_metrics import instant
    from store_time import utc_iso

    params = dict(timeType="createdAt", startAt=utc_iso(start),
                  endAt=utc_iso(end), limit=100)
    ids, cursors, counts = set(), set(), set()
    duplicate_count = outside_count = 0
    for page_number in range(1, 101):
        page = await client.signal_get("transactions", params=params)
        count = page.get("count")
        if type(count) is int:
            counts.add(count)
        rows = page.get("transactions", [] if count == 0 else None)
        if not isinstance(rows, list):
            raise ValueError("Unexpected Poynt transaction page")
        for row in rows:
            if not start <= instant(row["createdAt"]) < end:
                outside_count += 1
                continue
            identifier = row["id"]
            duplicate_count += identifier in ids
            ids.add(identifier)
        links = [link for link in page.get("links") or [] if link.get("rel") == "next"]
        if not links:
            return ids, sorted(counts), page_number, duplicate_count, outside_count
        if len(links) != 1:
            raise ValueError("Multiple Poynt next links")
        tokens = parse_qs(urlsplit(links[0].get("href") or "").query).get("startFrom", [])
        if len(tokens) != 1 or tokens[0] in cursors:
            raise ValueError("Missing or repeated Poynt continuation cursor")
        cursors.add(tokens[0])
        params = {**params, "startFrom": tokens[0]}
    raise ValueError("Poynt scan exceeded 100 pages")


async def main():
    from poynt.client import PoyntClient, PoyntSignalAPIError
    from poynt.connection import get_poynt_credentials
    from store_time import local_day_bounds

    organization_id = 1
    day = date(2025, 12, 6)
    zone = ZoneInfo("America/Phoenix")
    credentials = get_poynt_credentials(organization_id)
    if not credentials:
        raise SystemExit("No Poynt connection for organization 1")
    client = PoyntClient(credentials, organization_id)
    start, end = local_day_bounds(day, zone)
    hourly_ids = set()
    try:
        daily_ids, counts, pages, duplicates, outside = await scan(client, start, end)
        print(f"{day} Phoenix full day: unique={len(daily_ids)} reported={counts} "
              f"pages={pages} duplicates={duplicates} outside_window={outside}", flush=True)
        lower = start
        while lower < end:
            upper = min(lower + timedelta(hours=1), end)
            ids, counts, pages, duplicates, outside = await scan(client, lower, upper)
            print(f"{lower.astimezone(zone):%H:%M}-{upper.astimezone(zone):%H:%M}: "
                  f"unique={len(ids)} reported={counts} pages={pages} duplicates={duplicates} "
                  f"outside_window={outside} hourly_only={len(ids - daily_ids)}", flush=True)
            hourly_ids.update(ids)
            lower = upper
    except PoyntSignalAPIError as exc:
        raise SystemExit(f"Poynt HTTP {exc.status_code}; retry after at least {exc.retry_after}s.") from None
    print(f"Comparison: full_day={len(daily_ids)} hourly_union={len(hourly_ids)} "
          f"hourly_only={len(hourly_ids - daily_ids)} full_day_only={len(daily_ids - hourly_ids)}")


if __name__ == "__main__":
    env_file = ".env.local-prod-db"
    url = dotenv_values(env_file).get("DATABASE_URL")
    if not url:
        raise SystemExit(f"DATABASE_URL is missing from {env_file}")
    os.environ.update(DATABASE_URL=url, DOTENV_FILE=env_file)
    asyncio.run(main())
