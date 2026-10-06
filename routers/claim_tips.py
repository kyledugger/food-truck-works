import json
import secrets
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from database import SessionLocal
from organization_context import get_current_organization_id
from claim_tip_access import claim_stores, require_claim_store, latest_claim_end
from claim_tip_windows import minute_floor, window_bounds, parse_start, validate_window
from store_time import utc_iso
from tip_submission_model import TipOrderClaim
from routers.poynt import (get_poynt_credentials, fetch_poynt_orders, filter_completed_orders,
    _active_store_order, _parse_tip_submission_datetime, _store_tip_setting,
    _validate_tip_submission_window, get_organization_role, get_tip_calculator_data,
    get_tip_calculator_employees)

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def claim_context(request, token):
    context = request.session.get("tip_claim")
    if (not context or not secrets.compare_digest(context["token"], token)
            or context["organization_id"] != get_current_organization_id(request)
            or context["user_id"] != request.session.get("user_id")):
        raise HTTPException(409, "This claim session has changed. Launch Claim Tips again.")
    return context


@router.get("/poynt/claim-tips")
def claim_setup(request: Request, store_id: str = "", token: str = "", day: str = ""):
    organization_id = get_current_organization_id(request)
    if not request.session.get("user_id") or organization_id is None:
        return RedirectResponse("/login", status_code=303)
    if token:
        context = claim_context(request, token)
    else:
        context = {"token": secrets.token_urlsafe(24), "organization_id": organization_id,
            "user_id": request.session["user_id"], "launched": utc_iso(minute_floor(datetime.now(timezone.utc)))}
        request.session["tip_claim"] = context
    launched = _parse_tip_submission_datetime(context["launched"])
    with SessionLocal() as session:
        stores, manager, display = claim_stores(session, request, organization_id)
        if display or (not store_id and len(stores) == 1):
            if display and store_id and store_id.lower() != stores[0].store_id.lower():
                raise HTTPException(403, "This display can only claim its assigned store.")
            store_id = stores[0].store_id
        selected = None
        options = []
        for store in stores:
            options.append({"id": store.store_id, "name": store.display_name or store.poynt_name})
        if store_id:
            store, manager, display = require_claim_store(session, request, organization_id, store_id)
            setting = _store_tip_setting(session, organization_id, store.store_id)
            today = launched.astimezone(ZoneInfo(store.timezone_name)).date()
            last = latest_claim_end(session, organization_id, store.store_id, launched)
            suggested_day = today
            if last and launched - timedelta(hours=24) <= last < launched:
                suggested_day = last.astimezone(ZoneInfo(store.timezone_name)).date()
            if day:
                try:
                    suggested_day = date.fromisoformat(day)
                    _, end_limit = window_bounds(launched, store.timezone_name, suggested_day, manager)
                    last = latest_claim_end(session, organization_id, store.store_id, end_limit)
                except ValueError as exc:
                    raise HTTPException(400, str(exc)) from exc
            lower, upper = window_bounds(launched, store.timezone_name, suggested_day, manager,
                last, setting.tip_allocation_start_at if setting else None)
            selected = {"id": store.store_id, "name": store.display_name or store.poynt_name,
                "timezone": store.timezone_name, "today": today.isoformat(),
                "yesterday": (today - timedelta(days=1)).isoformat(),
                "suggested_date": suggested_day.isoformat(),
                "suggested_time": lower.astimezone(ZoneInfo(store.timezone_name)).strftime("%H:%M"),
                "last": utc_iso(last), "activation": utc_iso(setting.tip_allocation_start_at) if setting else None,
                "launched": context["launched"], "enabled": bool(setting and setting.tip_allocation_start_at)}
    return templates.TemplateResponse(request=request, name="claim_tips.html", context={
        "stores": options, "selected": selected, "manager": manager, "display": display,
        "token": context["token"]}, headers={"Cache-Control": "no-store"})


@router.post("/poynt/claim-tips/load")
async def load_claim(request: Request, store_id: str = Form(...), token: str = Form(...),
                     start_date: str = Form(...), start_time: str = Form(...)):
    context = claim_context(request, token)
    organization_id = context["organization_id"]
    launched = _parse_tip_submission_datetime(context["launched"])
    with SessionLocal() as session:
        store, manager, display = require_claim_store(session, request, organization_id, store_id)
        store_id = store.store_id
        setting = _store_tip_setting(session, organization_id, store_id)
        try:
            day = date.fromisoformat(start_date)
            start = parse_start(day, start_time, store.timezone_name)
            _, end_limit = window_bounds(launched, store.timezone_name, day, manager)
            lower, end = window_bounds(launched, store.timezone_name, day, manager,
                latest_claim_end(session, organization_id, store_id, end_limit),
                setting.tip_allocation_start_at if setting else None)
            validate_window(start, end, lower, end)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        role = get_organization_role(request.session["user_id"], organization_id)
        _validate_tip_submission_window(setting, start, role)
        store_name, zone = store.display_name or store.poynt_name, store.timezone_name
        policy = setting.payout_policy
        activation, hours = utc_iso(setting.tip_allocation_start_at), setting.employee_submission_hours
    credentials = get_poynt_credentials(organization_id)
    if credentials is None:
        raise HTTPException(409, "Connect Poynt before claiming tips.")
    try:
        orders = await fetch_poynt_orders(credentials, organization_id, start.isoformat(), end.isoformat())
    except Exception as exc:
        raise HTTPException(502, "Could not load current tips. Try again.") from exc
    orders = [o for o in filter_completed_orders(orders)[0] if _active_store_order(o, store_id)
        and o.get("createdAt") and start <= _parse_tip_submission_datetime(o["createdAt"]) < end]
    with SessionLocal() as session:
        claimed_ids = set(session.scalars(select(TipOrderClaim.poynt_order_id).where(
            TipOrderClaim.organization_id == organization_id,
            TipOrderClaim.poynt_business_id == credentials.business_id,
            TipOrderClaim.poynt_order_id.in_([o["id"] for o in orders]))))
    available = [o for o in orders if o["id"] not in claimed_ids]
    total = sum(o["tip_cents"] for o in get_tip_calculator_data(orders))
    available_total = sum(o["tip_cents"] for o in get_tip_calculator_data(available))
    employees = get_tip_calculator_employees(organization_id)
    context = dict(context, store_id=store_id, start=utc_iso(start), end=utc_iso(end))
    request.session["tip_claim"] = context
    return templates.TemplateResponse(request=request, name="claim_tips_calculator.html", context={
        "token": token, "store_timezone": zone, "tip_calculator_store_id": store_id,
        "tip_calculator_store_name": store_name, "tip_calculator_data": get_tip_calculator_data(available),
        "tip_calculator_employees": employees, "tip_calculator_enabled": bool(employees and available_total > 0),
        "start_at_for_tip_calculator": utc_iso(start), "end_at_for_tip_calculator": utc_iso(end),
        "tip_payout_policy": policy, "tip_allocation_start_at": activation,
        "tip_employee_submission_hours": hours, "tip_employee_window_exempt": manager,
        "total_tips": total / 100, "claimed_tips": (total - available_total) / 100,
        "available_tips": available_total / 100, "display": display,
    }, headers={"Cache-Control": "no-store"})
