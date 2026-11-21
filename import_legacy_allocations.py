"""Preview and apply legacy Ice Cream Crush allocations to FTW."""
import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from store_time import local_day_bounds, local_to_utc, utc_now

ZONE = ZoneInfo("America/Phoenix")


def cents(value):
    if value is None:
        return None
    return int((Decimal(str(value)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def normalized_id(value):
    return value.strip().lower().removeprefix("#")


def month_range(value):
    if len(value) != 7:
        raise ValueError("Use YYYY-MM for the month.")
    first = date.fromisoformat(value + "-01")
    return first, (first.replace(day=28) + timedelta(days=4)).replace(day=1)


def load_legacy(db_path, events_path, first, end):
    legacy = []
    for index, event in enumerate(json.loads(events_path.read_text(encoding="utf-8-sig"))["Events"], 1):
        day = datetime.strptime(event["Date"], "%m/%d/%Y").date()
        if not first <= day < end:
            continue
        start, stop = [
            datetime.strptime(f"{event['Date']} {event[key]}", "%m/%d/%Y %I:%M %p")
            for key in ("StartTime", "EndTime")
        ]
        if stop < start:
            stop += timedelta(days=1)
        ids = {str(value).strip() for value in event.get("Terminal_ids") or [] if str(value).strip()}
        legacy.append((index, event, start, stop, ids))
    if not legacy:
        raise ValueError("No legacy events found for the selected month.")

    lower = min(item[2] for item in legacy)
    upper = max(item[3] for item in legacy) + timedelta(minutes=1)
    with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute(
            """SELECT transaction_id, timestamp, terminal_id, subtotal, total
               FROM transactions WHERE timestamp >= ? AND timestamp < ?
               AND lower(trim(status)) = 'captured' ORDER BY timestamp, transaction_id""",
            (lower.isoformat(), upper.isoformat()),
        ).fetchall()
    sales = [(normalized_id(key), datetime.fromisoformat(when), str(terminal).strip(),
              cents(subtotal), cents(total)) for key, when, terminal, subtotal, total in rows]
    return legacy, sales, upper


def make_plan(legacy, sales, ftw_sales, existing_events):
    candidates = defaultdict(list)
    for row in ftw_sales:
        key = normalized_id(row.transaction_id)
        candidates[key[:8]].append(row)
        if len(key) != 8:
            candidates[key].append(row)

    plans = []
    event_use = Counter()
    legacy_use = Counter()
    ftw_use = Counter()
    for number, event, start, stop, terminals in legacy:
        # The old export stored minute precision and used an inclusive end.
        rows = [sale for sale in sales if sale[2] in terminals and start <= sale[1] <= stop]
        legacy_use.update((sale[0], sale[1]) for sale in rows)
        identity = (start.date(), *(str(event.get(key) or "").strip() for key in ("Name", "City", "County")))
        event_use[identity] += 1
        existing = existing_events.get(identity, [])
        issues = Counter()
        proposed = []
        if not terminals:
            issues["missing_legacy_terminal"] += 1
        if not rows:
            issues["no_legacy_sales"] += 1
        if len(existing) > 1:
            issues["duplicate_ftw_events"] += 1
        if not all(0 < len(value) <= maximum for value, maximum in zip(identity[1:], (200, 100, 100))):
            issues["invalid_event_fields"] += 1
        expected_count = event.get("_TransactionCount")
        expected_total = cents(event.get("_TotalRevenue"))
        legacy_total = sum(sale[4] or 0 for sale in rows)
        if expected_count is not None and expected_count != len(rows):
            issues["legacy_count_mismatch"] += 1
        if expected_total is not None and expected_total != legacy_total:
            issues["legacy_total_mismatch"] += 1

        for key, when, _, subtotal, _ in rows:
            possibilities = candidates.get(key, [])
            if len(possibilities) != 1:
                issues["missing_ftw_id" if not possibilities else "ambiguous_ftw_id"] += 1
                continue
            row = possibilities[0]
            local_minute = row.created_at.astimezone(ZONE).replace(tzinfo=None, second=0, microsecond=0)
            if local_minute != when.replace(second=0, microsecond=0):
                issues["sale_minute_mismatch"] += 1
                continue
            if subtotal is None or row.order_cents != subtotal:
                issues["sale_amount_mismatch"] += 1
                continue
            if row.event_id is not None and row.event_id not in existing:
                issues["claimed_by_another_event"] += 1
                continue
            proposed.append([row.business_id, row.transaction_id, row.order_cents])
            ftw_use[(row.business_id, row.transaction_id)] += 1
        plans.append((rows, {
            "number": number, "date": identity[0].isoformat(), "name": identity[1],
            "city": identity[2], "county": identity[3], "source_event": event,
            "legacy_sales": len(rows), "legacy_total_cents": legacy_total,
            "matched_before_tips_cents": sum(row[2] for row in proposed),
            "transactions": sorted(proposed), "issues": dict(issues),
        }))

    result = []
    for rows, plan in plans:
        identity = (date.fromisoformat(plan["date"]), plan["name"], plan["city"], plan["county"])
        if event_use[identity] > 1:
            plan["issues"]["duplicate_legacy_event_identity"] = event_use[identity]
        shared_legacy = sum(legacy_use[(row[0], row[1])] > 1 for row in rows)
        shared_ftw = sum(ftw_use[tuple(row[:2])] > 1 for row in plan["transactions"])
        if shared_legacy:
            plan["issues"]["overlapping_legacy_sales"] = shared_legacy
        if shared_ftw:
            plan["issues"]["overlapping_ftw_sales"] = shared_ftw
        result.append(plan)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["preview", "apply"])
    parser.add_argument("--sqlite", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--month", required=True)
    parser.add_argument("--org", required=True, type=int)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--actor", type=int, help="FTW owner or manager user ID for apply")
    parser.add_argument("--event", type=int, action="append", help="Reviewed event number; repeat as needed")
    args = parser.parse_args()
    if args.mode == "apply" and (not args.actor or not args.event):
        parser.error("Apply requires --actor and at least one --event from the preview.")
    try:
        run(args)
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"{exc}\n")


def run(args):
    from dotenv import dotenv_values
    from sqlalchemy import and_, insert, select, text
    from sqlalchemy.exc import IntegrityError

    url = dotenv_values(args.env_file).get("DATABASE_URL")
    if not url:
        raise ValueError("DATABASE_URL is missing from the selected environment file.")
    os.environ.update(DATABASE_URL=url, DOTENV_FILE=str(args.env_file))
    from database import SessionLocal, engine
    from models import Organization, OrganizationMember
    from allocation_models import allocation_claims as claims, allocation_events as events
    from permissions import role_can_manage_organization
    from poynt.transaction_history import transactions

    first, end = month_range(args.month)
    legacy, old_sales, upper = load_legacy(args.sqlite, args.events, first, end)
    start_at, _ = local_day_bounds(first, ZONE)
    end_at = max(local_day_bounds(end, ZONE)[0], local_to_utc(upper, ZONE))
    tc, cc, ec = transactions.c, claims.c, events.c
    target = sha256(engine.url.set(password=None).render_as_string().encode()).hexdigest()
    destination = {"version": 1, "database": target, "organization": args.org, "month": args.month}
    try:
        with SessionLocal.begin() as session:
            if args.mode == "preview":
                session.execute(text("SET TRANSACTION READ ONLY"))
            else:
                if session.scalar(select(Organization.id).where(
                    Organization.id == args.org
                ).with_for_update()) is None:
                    raise ValueError("FTW organization not found.")
                role = session.scalar(select(OrganizationMember.role).where(
                    OrganizationMember.organization_id == args.org,
                    OrganizationMember.user_id == args.actor,
                ))
                if not role_can_manage_organization(role):
                    raise ValueError("The actor must be an owner or manager of this organization.")
            existing = defaultdict(list)
            for row in session.execute(select(events).where(
                ec.organization_id == args.org, ec.event_date >= first, ec.event_date < end,
            )):
                existing[(row.event_date, row.name, row.city, row.county)].append(row.id)
            statement = select(tc.business_id, tc.transaction_id, tc.created_at, tc.order_cents, cc.event_id).select_from(
                transactions.outerjoin(claims, and_(
                    cc.organization_id == tc.organization_id, cc.business_id == tc.business_id,
                    cc.transaction_id == tc.transaction_id,
                ))
            ).where(
                tc.organization_id == args.org, tc.created_at >= start_at, tc.created_at < end_at,
                tc.status == "CAPTURED", tc.action == "SALE", tc.currency == "USD", tc.voided.is_not(True),
            )
            if args.mode == "apply":
                statement = statement.order_by(tc.business_id, tc.transaction_id).with_for_update(of=transactions)
            ftw_sales = session.execute(statement).all()
            plans = make_plan(legacy, old_sales, ftw_sales, existing)

            if args.mode == "preview":
                with args.plan.open("x", encoding="utf-8") as output:
                    json.dump({**destination, "events": plans}, output, indent=2, ensure_ascii=False)
                saved_claims = sum(
                    row.event_id is not None for row in ftw_sales
                )
                for plan in plans:
                    status = ", ".join(f"{key}={value}" for key, value in plan["issues"].items()) or "ready"
                    print(f"{plan['number']:>3}  {plan['date']}  {plan['name']}: "
                          f"{len(plan['transactions'])}/{plan['legacy_sales']} matched | "
                          f"FTW before tips ${Decimal(plan['matched_before_tips_cents']) / 100:,.2f} | "
                          f"legacy total ${Decimal(plan['legacy_total_cents']) / 100:,.2f} | {status}")
                ready = [plan for plan in plans if not plan["issues"]]
                print(f"{len(ready)}/{len(plans)} events ready, "
                      f"{sum(len(plan['transactions']) for plan in ready)} sales in ready events.")
                print(f"{saved_claims} eligible FTW sales already have claims in the scanned period.")
                print(f"Preview written to {args.plan}. No FTW changes.")
                return

            reviewed = json.loads(args.plan.read_text(encoding="utf-8"))
            if any(reviewed.get(key) != value for key, value in destination.items()):
                raise ValueError("This preview belongs to a different database, organization, or month.")
            wanted = set(args.event)
            prior = {plan["number"]: plan for plan in reviewed["events"]}
            current = {plan["number"]: plan for plan in plans}
            for number in sorted(wanted):
                plan = current.get(number)
                if not plan or plan["issues"] or prior.get(number) != plan:
                    raise ValueError(f"Event {number} has issues or changed. Generate a fresh preview.")
            claimed = {(row.business_id, row.transaction_id): row.event_id for row in ftw_sales}
            created = added = 0
            now = utc_now()
            for number in sorted(wanted):
                plan = current[number]
                identity = (date.fromisoformat(plan["date"]), plan["name"], plan["city"], plan["county"])
                event_date, name, city, county = identity
                matches = existing.get(identity)
                if matches:
                    event_id = matches[0]
                else:
                    event_id = session.execute(insert(events).values(
                        organization_id=args.org, event_date=event_date, name=name, city=city, county=county,
                    ).returning(ec.id)).scalar_one()
                    existing[identity].append(event_id)
                    created += 1
                additions = [
                    dict(organization_id=args.org, business_id=business, transaction_id=tx_id,
                         event_id=event_id, claimed_by=args.actor, claimed_at=now)
                    for business, tx_id, _ in plan["transactions"] if claimed[(business, tx_id)] is None
                ]
                if additions:
                    session.execute(insert(claims), additions)
                    added += len(additions)
    except IntegrityError:
        raise ValueError("An allocation changed during apply. The batch was rolled back. Generate a fresh preview.") from None
    print(f"Applied {len(wanted)} events. Created {created} events and added {added} claims.")


if __name__ == "__main__":
    main()
