"""Review captured Poynt sales and claim exact rows for a historical event."""
from datetime import date, timedelta
from hmac import compare_digest
from secrets import token_urlsafe
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, insert, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from allocation_models import allocation_claims as claims, allocation_events as events
from database import SessionLocal
from models import OrganizationStore
from organization_context import get_current_organization_id
from permissions import get_organization_role, role_can_manage_organization
from poynt.transaction_history import transactions
from store_time import local_day_bounds, utc_now

router = APIRouter(prefix="/tpt/allocation")
templates = Jinja2Templates(directory="templates")
ZONE = ZoneInfo("America/Phoenix")
tc = transactions.c
cc = claims.c
ec = events.c


def manager(request):
    user_id = request.session.get("user_id")
    org_id = get_current_organization_id(request) if user_id else None
    if org_id is None or not role_can_manage_organization(get_organization_role(user_id, org_id)):
        raise HTTPException(403, "Owner or manager access is required.")
    return org_id, user_id


def sales_in_period(org_id, start, end):
    return and_(
        tc.organization_id == org_id, tc.created_at >= start, tc.created_at < end,
        tc.status == "CAPTURED", tc.action == "SALE", tc.currency == "USD",
        tc.voided.is_not(True),
    )


def eligible_sales(org_id, day):
    return sales_in_period(org_id, *local_day_bounds(day, ZONE))


def sales_with_claims():
    return transactions.outerjoin(claims, and_(
        cc.organization_id == tc.organization_id,
        cc.business_id == tc.business_id,
        cc.transaction_id == tc.transaction_id,
    )).outerjoin(events, and_(ec.id == cc.event_id, ec.organization_id == tc.organization_id))


def bundles_for(rows):
    grouped = {}
    for row in rows:
        key = row.business_id, row.store_id, row.terminal_id
        bundles = grouped.setdefault(key, [])
        if not bundles or row.created_at - bundles[-1][-1].created_at > timedelta(minutes=60):
            bundles.append([])
        bundles[-1].append(row)
    return sorted((bundle for bundles in grouped.values() for bundle in bundles),
                  key=lambda bundle: bundle[0].created_at)


def amount(cents):
    if cents is None:
        return "Unknown"
    dollars, remainder = divmod(abs(cents), 100)
    return f"{'-' if cents < 0 else ''}${dollars:,}.{remainder:02d}"


templates.env.filters["allocation_amount"] = amount


def saved_place_names(session, org_id, column):
    names = {}
    for value in session.scalars(
        select(column).where(ec.organization_id == org_id).distinct().order_by(column)
    ):
        name = " ".join(value.split())
        if name:
            names.setdefault(name.casefold(), name)
    return names


@router.get("")
def allocation_page(request: Request, day: date = date(2025, 12, 6), notice: str = ""):
    org_id, _ = manager(request)
    request.session.setdefault("allocation_csrf", token_urlsafe(32))
    with SessionLocal() as session:
        rows = session.execute(
            select(transactions, ec.id.label("event_id"), ec.name.label("event_name"), ec.city, ec.county)
            .select_from(sales_with_claims())
            .where(eligible_sales(org_id, day))
            .order_by(tc.created_at, tc.business_id, tc.transaction_id)
        ).all()
        day_events = session.execute(select(events).where(
            ec.organization_id == org_id, ec.event_date == day,
        ).order_by(ec.name, ec.id)).all()
        store_names = {
            store_id: (display_name or "").strip() or poynt_name
            for store_id, display_name, poynt_name in session.execute(
                select(OrganizationStore.store_id, OrganizationStore.display_name,
                       OrganizationStore.poynt_name)
                .where(OrganizationStore.organization_id == org_id)
            )
        }
        cities = saved_place_names(session, org_id, ec.city)
        counties = saved_place_names(session, org_id, ec.county)

    unassigned = [row for row in rows if row.event_id is None]
    totals = {}
    assigned_transactions = {}
    for row in rows:
        if row.event_id is not None:
            key = row.event_id, row.event_name, row.city, row.county
            assigned_transactions.setdefault(row.event_id, []).append(row)
            tally = totals.setdefault(key, [0, 0])
            tally[0] += 1
            tally[1] += row.order_cents or 0
    return templates.TemplateResponse(request=request, name="transaction_allocation.html", context={
        "day": day, "zone": ZONE, "notice": notice, "events": day_events,
        "previous_day": day - timedelta(days=1) if day > date.min else None,
        "next_day": day + timedelta(days=1) if day < date.max - timedelta(days=1) else None,
        "store_names": store_names,
        "cities": sorted(cities.values(), key=str.casefold),
        "counties": sorted(counties.values(), key=str.casefold),
        "csrf_token": request.session["allocation_csrf"], "bundles": bundles_for(unassigned),
        "total_count": len(rows), "unassigned_count": len(unassigned),
        "total_cents": sum(row.order_cents or 0 for row in rows),
        "unassigned_cents": sum(row.order_cents or 0 for row in unassigned),
        "missing_amount": sum(row.order_cents is None for row in rows),
        "assigned_events": totals, "assigned_transactions": assigned_transactions,
    })


def summarize_month(first, end, rows):
    days = {
        first + timedelta(days=offset): {
            "sales": 0, "assigned": 0, "unassigned": 0,
            "assigned_cents": 0, "unassigned_cents": 0, "missing_amount": 0,
        }
        for offset in range((end - first).days)
    }
    counties = {}
    for row in rows:
        day = row.created_at.astimezone(ZONE).date()
        daily = days[day]
        daily["sales"] += 1
        daily["missing_amount"] += row.order_cents is None
        cents = row.order_cents or 0
        if row.event_id is None:
            daily["unassigned"] += 1
            daily["unassigned_cents"] += cents
            continue
        daily["assigned"] += 1
        daily["assigned_cents"] += cents
        county = counties.setdefault(row.county or "Unknown county", {"sales": 0, "cents": 0, "cities": {}})
        county["sales"] += 1
        county["cents"] += cents
        place = county["cities"].setdefault(row.city or "Unknown city", {"sales": 0, "cents": 0, "events": {}})
        place["sales"] += 1
        place["cents"] += cents
        event = place["events"].setdefault((day, row.event_id, row.event_name), {"sales": 0, "cents": 0})
        event["sales"] += 1
        event["cents"] += cents
    totals = {key: sum(daily[key] for daily in days.values()) for key in next(iter(days.values()))}
    return days, totals, counties


@router.get("/summary")
def monthly_summary(request: Request, month: str = "2025-11"):
    org_id, _ = manager(request)
    try:
        first = date.fromisoformat(f"{month}-01")
        if first.isoformat()[:7] != month:
            raise ValueError("Invalid month")
        end = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
        start_at = local_day_bounds(first, ZONE)[0]
        end_at = local_day_bounds(end, ZONE)[0]
    except (ValueError, OverflowError) as exc:
        raise HTTPException(422, "Choose a month in YYYY-MM format.") from exc

    with SessionLocal() as session:
        rows = session.execute(
            select(tc.created_at, tc.order_cents, ec.id.label("event_id"),
                   ec.name.label("event_name"), ec.city, ec.county)
            .select_from(sales_with_claims())
            .where(sales_in_period(org_id, start_at, end_at))
            .order_by(tc.created_at, ec.id)
        ).all()
    days, totals, counties = summarize_month(first, end, rows)
    return templates.TemplateResponse(request=request, name="transaction_allocation_summary.html", context={
        "month": month, "month_name": first.strftime("%B %Y"), "first": first,
        "days": days, "totals": totals, "counties": sorted(counties.items()),
    })


@router.post("/claim")
def claim_sales(
    request: Request, day: date = Form(...), csrf_token: str = Form(...),
    sale_keys: list[str] = Form(default=[]), event_id: int = Form(0),
    name: str = Form(""), city: str = Form(""), county: str = Form(""),
):
    org_id, user_id = manager(request)
    expected = request.session.get("allocation_csrf")
    if not expected or not compare_digest(expected, csrf_token):
        raise HTTPException(403, "Invalid form token. Reload the allocation screen.")
    keys = {tuple(key.split("|", 1)) for key in sale_keys}
    if not keys or any(len(key) != 2 or not all(0 < len(part) <= 100 for part in key) for key in keys):
        raise HTTPException(400, "Select one or more sales.")
    name = name.strip()
    city, county = " ".join(city.split()), " ".join(county.split())
    if event_id == 0 and not (0 < len(name) <= 200 and 0 < len(city) <= 100 and 0 < len(county) <= 100):
        raise HTTPException(400, "Enter the event name, city, and county.")

    with SessionLocal.begin() as session:
        verified = set(session.execute(select(tc.business_id, tc.transaction_id).where(
            eligible_sales(org_id, day), tuple_(tc.business_id, tc.transaction_id).in_(keys),
        )).all())
        if verified != keys:
            raise HTTPException(409, "A selected sale is unavailable for this date. Reload and try again.")
        if event_id == 0:
            city = saved_place_names(session, org_id, ec.city).get(city.casefold(), city)
            county = saved_place_names(session, org_id, ec.county).get(county.casefold(), county)
            event_id = session.execute(insert(events).values(
                organization_id=org_id, event_date=day, name=name, city=city, county=county,
            ).returning(ec.id)).scalar_one()
        elif session.scalar(select(ec.id).where(
            ec.id == event_id, ec.organization_id == org_id, ec.event_date == day,
        )) is None:
            raise HTTPException(404, "Event not found for this date.")
        now = utc_now()
        statement = pg_insert(claims).values([
            {"organization_id": org_id, "business_id": business, "transaction_id": tx_id,
             "event_id": event_id, "claimed_by": user_id, "claimed_at": now}
            for business, tx_id in sorted(keys)
        ]).on_conflict_do_nothing(index_elements=list(claims.primary_key.columns)).returning(cc.transaction_id)
        if len(session.execute(statement).all()) != len(keys):
            raise HTTPException(409, "A selected sale was already assigned. Reload and try again.")
    return RedirectResponse(f"/tpt/allocation?day={day}&notice=claimed", status_code=303)
