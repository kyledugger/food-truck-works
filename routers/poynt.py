from sku_map import fix_sku
from categories import sku_prefix_to_category_map
import json
from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from poynt.token import exchange_authorization_code
from zoneinfo import ZoneInfo
from store_time import as_utc, local_to_utc, local_day_bounds, utc_iso

from poynt.client import (
    PoyntClient,
    PoyntReauthorizationRequired,
)

from dotenv import load_dotenv
import os
from database import SessionLocal
from models import Employee, OrganizationMember, OrganizationStore, User
from tip_submission_model import TipSubmission, TipStoreSettings, TipOrderClaim, TipEmployeePayout
from poynt.connection import get_poynt_connection, get_poynt_credentials
from organization_context import get_current_organization_id
from permissions import (
    get_organization_role,
    role_can_manage_integrations,
    role_can_view_payroll_reports,
    role_can_manage_organization,
)

dotenv_file = os.getenv("DOTENV_FILE", ".env")
load_dotenv(dotenv_file)

from logging_config import configure_logging

import logging
logger = logging.getLogger(__name__)

POYNT_REDIRECT_URI = os.environ["POYNT_REDIRECT_URI"]
POYNT_APP_ID = os.environ["POYNT_APP_ID"]
POYNT_AUTHORIZE_URL = os.environ["POYNT_AUTHORIZE_URL"]

router = APIRouter()

templates = Jinja2Templates(directory="templates")


@router.get("/settings/integrations/poynt", response_class=HTMLResponse)
async def poynt_settings(request: Request):
    user_id = request.session.get("user_id")

    if not user_id:
        return RedirectResponse("/login", status_code=303)

    organization_id = get_current_organization_id(request)
    if organization_id is None:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    role = get_organization_role(user_id, organization_id)
    if not role_can_manage_integrations(role):
        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "Poynt Access Denied",
                "paragraphs": [
                    "Only organization owners and managers can manage the Poynt connection."
                ],
                "show_dashboard_link": True,
            },
            status_code=403,
        )

    connection = get_poynt_connection(organization_id)

    return templates.TemplateResponse(
        request=request,
        name="poynt_settings.html",
        context={
            "organization_role": role,
            "poynt_connection": connection,
        },
    )


@router.get("/poynt/catalog", response_class=HTMLResponse)
async def poynt_catalog(request: Request):

    user_id = request.session.get("user_id")

    if not user_id:
        return RedirectResponse(
            "/login",
            status_code=303
        )

    organization_id = get_current_organization_id(request)

    if organization_id is None:
        request.session.clear()
        return RedirectResponse(
            "/login",
            status_code=303
        )

    credentials = get_poynt_credentials(organization_id)

    if not credentials:
        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "Poynt Error",
                "paragraphs": [
                    "No Poynt connection was found."
                ],
                "show_dashboard_link": True,
            },
            status_code=404,
        )

    try:
        client = PoyntClient(
            credentials,
            organization_id=organization_id,
        )


        catalogs = await client.get_catalogs()

    except PoyntReauthorizationRequired:
        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "Poynt Authorization Required",
                "paragraphs": [
                    "Your Poynt authorization has expired.",
                    "Please reconnect your Poynt account.",
                ],
                "show_dashboard_link": True,
            },
            status_code=401,
        )

    except Exception as e:
        print(
            f"Poynt catalog request failed: "
            f"{type(e).__name__}: {e}",
            flush=True
        )

        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "Poynt Catalog Error",
                "paragraphs": [
                    "The catalog request failed.",
                    "Check the application logs.",
                ],
                "show_dashboard_link": True,
            },
            status_code=502,
        )

    return templates.TemplateResponse(
        request=request,
        name="message.html",
        context={
            "title": "Poynt Catalog Success!",
            "paragraphs": [
                "Catalog request succeeded.",
                "The Poynt access token was retrieved from the database and used to make this request.",
            ],
            "show_dashboard_link": True,
        },
    )

def  get_prefix_counts(orders, sku_counts):   # Count units ordered by SKU prefix/category.
    prefix_counts = {}

    for order in orders:
        items = order.get("items") or []

        for item in items:
            sku = item.get("sku")


            if not sku:
                continue

            quantity = item.get("quantity", 0)

            try:
                quantity = float(quantity)
            except (TypeError, ValueError):
                quantity = 0

            # SKU count
            sku_counts[sku] = (
                sku_counts.get(sku, 0) + quantity
            )

            # Prefix/category count
            if "-" in sku:
                prefix = sku.split("-", 1)[0]
                prefix_counts[prefix] = (
                    prefix_counts.get(prefix, 0) + quantity
                )  

    return prefix_counts            


def format_duration(seconds):
    """
    Convert a duration in seconds into a human-friendly
    value + unit string.
    """

    if seconds is None:
        return "-"

    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return "-"

    if seconds < 60:
        value = round(seconds)
        unit = "second" if value == 1 else "seconds"

    elif seconds < 3600:
        value = round(seconds / 60, 1)

        # Remove unnecessary .0
        if value.is_integer():
            value = int(value)

        unit = "minute" if value == 1 else "minutes"

    elif seconds < 86400:
        value = round(seconds / 3600, 1)

        if value.is_integer():
            value = int(value)

        unit = "hour" if value == 1 else "hours"

    else:
        value = round(seconds / 86400, 1)

        if value.is_integer():
            value = int(value)

        unit = "day" if value == 1 else "days"

    return f"{value} {unit}"

def get_fastest_processing_times(orders):
    """
    Estimate best cashier processing pace by order complexity.

    For each order-complexity group:
      1 item
      2 items
      3 items

    Use the fastest 10% of valid order intervals, capped at 10
    intervals, and return the median of those intervals.

    The interval is attributed to the newer order.
    """

    chronological_orders = sorted(
        orders,
        key=lambda order: order.get("createdAt", "")
    )

    intervals_by_size = {
        "1": [],
        "2": [],
        "3": [],
    }

    previous_time = None

    for order in chronological_orders:
        created_at = order.get("createdAt")

        if not created_at:
            continue

        try:
            current_time = datetime.fromisoformat(
                created_at.replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            continue

        # We need a previous order to establish an interval.
        if previous_time is not None:
            interval_seconds = (
                current_time - previous_time
            ).total_seconds()

            # Count total units in this order.
            total_items = 0

            for item in order.get("items") or []:
                quantity = item.get("quantity", 0)

                try:
                    total_items += float(quantity)
                except (TypeError, ValueError):
                    continue

            if total_items == 1:
                group = "1"
            elif total_items == 2:
                group = "2"
            elif total_items == 3:
                group = "3"
            else:
                group = None

            if group is not None and interval_seconds >= 0:
                intervals_by_size[group].append(
                    interval_seconds
                )

        previous_time = current_time

    results = {}

    for group, intervals in intervals_by_size.items():

        if not intervals:
            results[group] = None
            continue

        # Fastest 10%, capped at 10 intervals.
        sample_size = min(
            10,
            max(1, (len(intervals) + 9) // 10)
        )

        fastest_intervals = sorted(intervals)[:sample_size]

        # Median without requiring another import.
        middle = len(fastest_intervals) // 2

        if len(fastest_intervals) % 2:
            median_seconds = fastest_intervals[middle]
        else:
            median_seconds = (
                fastest_intervals[middle - 1]
                + fastest_intervals[middle]
            ) / 2

        results[group] = median_seconds

    return results

def get_order_intervals(orders):
    # Build chronological order interval data for Chart #1.
    # Each point represents the number of seconds since the
    # previous order. The timestamp belongs to the newer order.
    chronological_orders = list(reversed(orders))

    order_intervals = []

    previous_time = None

    for order in chronological_orders:
        created_at = order.get("createdAt")

        if not created_at:
            continue

        try:
            current_time = datetime.fromisoformat(
                created_at.replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            continue

        if previous_time is not None:
            interval_seconds = (
                current_time - previous_time
            ).total_seconds()

            order_intervals.append({
                "time": created_at,
                "seconds": interval_seconds,
            })

        previous_time = current_time

    return order_intervals
    

def get_item_flow(orders):
    chronological_orders = list(reversed(orders))

    item_flow = {}

    first_bucket = None
    last_bucket = None

    for order in chronological_orders:
        created_at = order.get("createdAt")

        if not created_at:
            continue

        try:
            order_time = datetime.fromisoformat(
                created_at.replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            continue

        bucket_minute = (order_time.minute // 5) * 5

        bucket_time = order_time.replace(
            minute=bucket_minute,
            second=0,
            microsecond=0
        )

        if first_bucket is None:
            first_bucket = bucket_time

        last_bucket = bucket_time

        total_items = 0

        for item in order.get("items") or []:
            quantity = item.get("quantity", 0)

            try:
                total_items += float(quantity)
            except (TypeError, ValueError):
                continue

        item_flow[bucket_time] = (
            item_flow.get(bucket_time, 0) + total_items
        )

    if first_bucket is None:
        return []

    # Fill every five-minute bucket, including zero-activity buckets.
    result = []

    bucket_time = first_bucket

    while bucket_time <= last_bucket:
        result.append({
            "time": bucket_time.isoformat(),
            "items": item_flow.get(bucket_time, 0),
        })

        bucket_time += timedelta(minutes=5)

    return result

def get_available_store_ids(orders, store_names=None):
    """
    Get the store IDs found in the original order collection.
    """

    store_ids = set()

    for order in orders:
        for transaction in order.get("transactions") or []:
            context = transaction.get("context") or {}
            store_id = context.get("storeId")

            if store_id:
                store_ids.add(store_id.lower())

    return sorted(
        store_ids,
        key=lambda store_id: (store_names or {}).get(
            store_id,
            f"Unknown Store {store_id}",
        ).lower(),
    )


def filter_orders_by_stores(orders, stores):
    """
    Filter orders to those associated with the requested stores.

    If stores is None or empty, return all orders.
    """

    logger.info("filtering stores to: %s", stores)

    if not stores:
        return orders

    requested_stores = {
        store.lower()
        for store in stores
        if store
    }

    if not requested_stores:
        return orders

    filtered_orders = []

    for order in orders:
        for transaction in order.get("transactions") or []:
            context = transaction.get("context") or {}
            store_id = context.get("storeId")

            if store_id and store_id.lower() in requested_stores:
                filtered_orders.append(order)
                break

    logger.info(
        "store filtering reduced %s orders to %s orders",
        len(orders),
        len(filtered_orders),
    )

    return filtered_orders

def get_revenue_flow(orders):
    chronological_orders = list(reversed(orders))

    revenue_flow = {}
    first_bucket = None
    last_bucket = None

    for order in chronological_orders:
        created_at = order.get("createdAt")

        if not created_at:
            continue

        try:
            order_time = datetime.fromisoformat(
                created_at.replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            continue

        bucket_minute = (order_time.minute // 5) * 5

        bucket_time = order_time.replace(
            minute=bucket_minute,
            second=0,
            microsecond=0
        )

        if first_bucket is None:
            first_bucket = bucket_time

        last_bucket = bucket_time

        amounts = order.get("amounts") or {}

        total = amounts.get("netTotal")

        if total is None:
            total = amounts.get("orderAmount")

        try:
            revenue = float(total) / 100
        except (TypeError, ValueError):
            revenue = 0

        revenue_flow[bucket_time] = (
            revenue_flow.get(bucket_time, 0) + revenue
        )

    if first_bucket is None:
        return []

    # Fill every five-minute bucket, including zero-activity buckets.
    result = []

    bucket_time = first_bucket

    while bucket_time <= last_bucket:
        result.append({
            "time": bucket_time.isoformat(),
            "revenue": round(
                revenue_flow.get(bucket_time, 0),
                2
            ),
        })

        bucket_time += timedelta(minutes=5)

    return result

def get_sku_rows(sku_counts):
    """Prepare SKU count data for template rendering."""
    sku_rows = []

    for sku, quantity in sorted(
        sku_counts.items(),
        key=lambda x: (-x[1], x[0])
    ):
        if quantity.is_integer():
            quantity_display = str(int(quantity))
        else:
            quantity_display = str(quantity)

        sku_rows.append({
            "sku": fix_sku(str(sku)),
            "quantity": quantity_display,
        })

    return sku_rows


def get_category_rows(category_map, prefix_counts):
    """Prepare category count data for template rendering."""
    category_counts = {}

    for prefix, quantity in prefix_counts.items():
        category = category_map.get(prefix, prefix)

        category_counts[category] = (
            category_counts.get(category, 0) + quantity
        )

    category_rows = []

    for category, quantity in sorted(
        category_counts.items(),
        key=lambda x: (-x[1], x[0])
    ):
        if quantity.is_integer():
            quantity_display = str(int(quantity))
        else:
            quantity_display = str(quantity)

        category_rows.append({
            "category": str(category),
            "quantity": quantity_display,
        })

    return category_rows

def validate_and_convert_iso_datetime(iso_string: str):
    if iso_string == "":
        return None

    try:
        return datetime.fromisoformat(iso_string)

    except ValueError:
        logger.warning(
            "date/time parameter %s was not a valid ISO datetime string",
            iso_string
        )
        return None


def get_orders_date_range(start, end, store_timezone):
    start_at_date = validate_and_convert_iso_datetime(start)
    end_at_date = validate_and_convert_iso_datetime(end)

    if start_at_date and end_at_date:
        try:
            start_at_date = local_to_utc(start_at_date, ZoneInfo(store_timezone))
            end_at_date = local_to_utc(end_at_date, ZoneInfo(store_timezone))
        except ValueError:
            return {
                "error_title": "Ambiguous Local Time",
                "error_message": "Choose valid, unambiguous times in the store timezone.",
            }

    if not start:
        return {
            "error_title": "Start Date Required",
            "error_message": "A start date and time are required.",
        }

    if not end:
        return {
            "error_title": "End Date Required",
            "error_message": "An end date and time are required.",
        }

    if not start_at_date:
        return {
            "error_title": "Start Date Error",
            "error_message": "The start date and time are not valid.",
        }

    if not end_at_date:
        return {
            "error_title": "End Date Error",
            "error_message": "The end date and time are not valid.",
        }

    if end_at_date < start_at_date:
        return {
            "error_title": "Date Range Error",
            "error_message": "The end date and time must be after the start date and time.",
        }

    max_span = timedelta(days=3)

    if end_at_date - start_at_date > max_span:
        return {
            "error_title": "Date Range Too Long",
            "error_message": "The order report can cover a maximum of 3 days.",
        }


    span_seconds = end_at_date - start_at_date if start_at_date and end_at_date else None

    if span_seconds:
        span_seconds = span_seconds.total_seconds()
    
    start_at_utc = start_at_date.astimezone(timezone.utc)
    end_at_utc = end_at_date.astimezone(timezone.utc)

    return {
        "start_at": (
            start_at_utc
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        ),
        "end_at": (
            end_at_utc
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        ),
        "span_seconds": span_seconds,
    }


def filter_orders_by_created_at(orders, start_at, end_at):
    start_time = datetime.fromisoformat(
        start_at.replace("Z", "+00:00")
    )
    end_time = datetime.fromisoformat(
        end_at.replace("Z", "+00:00")
    )

    filtered_orders = []

    for order in orders:
        created_at = order.get("createdAt")

        if not created_at:
            continue

        try:
            created_time = datetime.fromisoformat(
                created_at.replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            logger.warning(
                "Invalid createdAt timestamp for order %s: %s",
                order.get("id"),
                created_at,
            )
            continue

        if start_time <= created_time <= end_time:
            filtered_orders.append(order)

    logger.info(
        "Created-at filtering reduced %d orders to %d orders.",
        len(orders),
        len(filtered_orders),
    )

    return filtered_orders

async def fetch_poynt_orders(
    credentials,
    organization_id,
    start_at,
    end_at,
):
    client = PoyntClient(
        credentials,
        organization_id=organization_id,
    )

    return await client.get_recent_orders(
        100,
        start_at=start_at,
        end_at=end_at,
        fetch_all=True,
    )


def filter_completed_orders(orders):
    completed_orders = []
    cancelled_order_count = 0

    for order in orders:
        statuses = order.get("statuses") or {}

        transaction_status = statuses.get(
            "transactionStatusSummary"
        )

        if transaction_status == "COMPLETED":
            completed_orders.append(order)
        else:
            cancelled_order_count += 1

    return completed_orders, cancelled_order_count


def calculate_order_metrics(orders):
    """
    Calculate the core metrics for a collection of completed orders.

    Returns raw numeric values and datetime objects.
    Formatting for display happens elsewhere.
    """

    total_revenue = 0.0
    total_items = 0.0
    total_tips = 0.0
    order_times = []

    for order in orders:
        amounts = order.get("amounts") or {}

        # Revenue
        total = amounts.get("netTotal")

        if total is None:
            total = amounts.get("orderAmount")

        try:
            total_revenue += float(total) / 100
        except (TypeError, ValueError):
            pass

        # Tips
        captured_totals = amounts.get("capturedTotals")

        if captured_totals:
            tip = captured_totals.get("tipAmount")
        else:
            tip = 0

        try:
            total_tips += float(tip) / 100
        except (TypeError, ValueError):
            pass

        # Items
        for item in order.get("items") or []:
            quantity = item.get("quantity", 0)

            try:
                total_items += float(quantity)
            except (TypeError, ValueError):
                pass

        # Order timestamp
        created_at = order.get("createdAt")

        if created_at:
            try:
                order_time = datetime.fromisoformat(
                    created_at.replace("Z", "+00:00")
                )
                order_times.append(order_time)
            except (TypeError, ValueError):
                logger.warning(
                    "missing created at for order: %s",
                    order
                )

    oldest_order_at = None
    newest_order_at = None
    order_span_seconds = None
    average_seconds_between_orders = None
    average_seconds_between_items = None
    items_per_order = None
    tip_ratio = None

    if order_times:
        oldest_order_at = min(order_times)
        newest_order_at = max(order_times)

        order_span_seconds = (
            newest_order_at - oldest_order_at
        ).total_seconds()

        if len(order_times) > 1:
            average_seconds_between_orders = (
                order_span_seconds / (len(order_times) - 1)
            )

        if total_items > 1:
            average_seconds_between_items = (
                order_span_seconds / (total_items - 1)
            )

    if orders:
        items_per_order = total_items / len(orders)

    if total_revenue:
        tip_ratio = total_tips / total_revenue

    return {
        "total_revenue": total_revenue,
        "total_items": total_items,
        "total_tips": total_tips,
        "oldest_order_at": oldest_order_at,
        "newest_order_at": newest_order_at,
        "order_span_seconds": order_span_seconds,
        "average_seconds_between_orders": average_seconds_between_orders,
        "average_seconds_between_items": average_seconds_between_items,
        "items_per_order": items_per_order,
        "tip_ratio": tip_ratio,
    }

def prepare_chart_data(orders):
    """
    Prepare chart data for the orders report.

    Returns raw chart data and JSON strings for use by JavaScript.
    """

    order_intervals = get_order_intervals(orders)
    item_flow = get_item_flow(orders)
    revenue_flow = get_revenue_flow(orders)

    return {
        "order_intervals": order_intervals,
        "order_intervals_json": json.dumps(order_intervals),
        "item_flow": item_flow,
        "item_flow_json": json.dumps(item_flow),
        "revenue_flow": revenue_flow,
        "revenue_flow_json": json.dumps(revenue_flow),
    }


def get_orders_data(orders):
    """
    Prepare order data for the orders report.

    Returns:
        orders_data: list of dictionaries containing order display data
        store_ids: set of store IDs found in the orders
    """

    orders_data = []
    store_ids = set()

    for order in orders:

        order_number = str(
            order.get(
                "orderNumber",
                order.get("id", "Unknown")
            )
        )

        transactions = order.get("transactions") or []

        for transaction in transactions:
            context = transaction.get("context") or {}
            store_id = context.get("storeId")

            if store_id:
                store_ids.add(store_id)

        created_at = str(
            order.get("createdAt", "")
        )

        amounts = order.get("amounts") or {}

        total = amounts.get("netTotal")

        if total is None:
            total = amounts.get("orderAmount")

        currency = amounts.get(
            "currency",
            "USD"
        )

        if total is not None:
            try:
                total_display = (
                    f"{currency} "
                    f"${int(total) / 100:.2f}"
                )
            except (TypeError, ValueError):
                total_display = str(total)
        else:
            total_display = "Unknown"

        notes = str(
            order.get("notes") or ""
        )

        items_data = []

        for item in order.get("items") or []:

            name = str(
                item.get("name") or ""
            )

            quantity = str(
                item.get("quantity") or ""
            )

            sku = str(
                item.get("sku") or ""
            )

            sku = fix_sku(sku)

            item_status = str(
                item.get("status") or ""
            )

            items_data.append({
                "name": name,
                "quantity": quantity,
                "sku": sku,
                "status": item_status,
            })

        orders_data.append({
            "order_number": order_number,
            "created_at": created_at,
            "total_display": total_display,
            "notes": notes,
            "items": items_data,
        })

    return orders_data, store_ids



def get_tip_calculator_data(orders):
    """
    Prepare the minimal order data needed by the in-browser
    tip calculator.

    Returns:
        list of dictionaries containing:
        - created_at: order timestamp
        - tip_cents: captured tip amount in cents
    """

    tip_data = []

    for order in orders:
        created_at = order.get("createdAt")

        if not created_at:
            continue

        amounts = order.get("amounts") or {}
        captured_totals = amounts.get("capturedTotals") or {}

        tip = captured_totals.get("tipAmount", 0)

        try:
            tip_cents = int(tip)
        except (TypeError, ValueError):
            tip_cents = 0

        tip_data.append({
            "created_at": created_at,
            "tip_cents": tip_cents,
        })

    return tip_data


def get_tip_calculator_employees(organization_id: int) -> list[dict]:
    """Return active employees available for tip allocation."""
    with SessionLocal() as session:
        employees = session.execute(
            select(Employee)
            .where(
                Employee.organization_id == organization_id,
                Employee.is_active.is_(True),
            )
            .order_by(Employee.last_name, Employee.first_name)
        ).scalars().all()

    return [
        {
            "id": employee.id,
            "name": f"{employee.first_name} {employee.last_name}",
        }
        for employee in employees
    ]

def get_stores_display(store_ids, store_names):
    """
    Convert a set of store IDs into display text.

    Returns a human-readable string containing the
    names of all stores found in the orders.
    """

    stores_display = ""
    first = True

    for store_id in store_ids:
        logger.debug("Store id %s found", store_id)

        if not first:
            stores_display += " + "

        if store_id.lower() not in store_names:
            stores_display += f"Unknown Store {store_id}"
            logger.warning("Unknown Store %s", store_id)
        else:
            stores_display += store_names[store_id.lower()]

        first = False

    return stores_display



def _parse_tip_submission_datetime(value: str) -> datetime:
    """Parse an ISO timestamp and normalize it to aware UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Tip timestamps must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _get_tip_report_date_bounds(start_date: str, end_date: str, store_timezone: str) -> tuple[datetime, datetime, str, str] | None:
    """Convert inclusive store calendar dates into UTC database bounds."""
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None

    if end < start:
        return None

    zone = ZoneInfo(store_timezone)
    try:
        start_utc, _ = local_day_bounds(start, zone)
        _, end_utc = local_day_bounds(end, zone)
    except ValueError:
        return None

    return (
        start_utc,
        end_utc,
        start.isoformat(),
        end.isoformat(),
    )


def _aggregate_tip_allocations(ranges: list) -> list[dict]:
    """Sum each employee's per-range allocation across a submission."""
    totals: dict[str, dict] = {}

    for tip_range in ranges:
        if not isinstance(tip_range, dict):
            continue

        employees = tip_range.get("employees") or []
        if not isinstance(employees, list) or not employees:
            continue

        try:
            raw_per_employee_cents = tip_range.get("per_employee_cents")
            if raw_per_employee_cents is None:
                raise ValueError
            per_employee_cents = int(raw_per_employee_cents)
        except (TypeError, ValueError):
            try:
                total_tip_cents = int(tip_range.get("total_tip_cents", 0))
            except (TypeError, ValueError):
                total_tip_cents = 0
            per_employee_cents = total_tip_cents // len(employees)

        for employee in employees:
            if not isinstance(employee, dict):
                continue

            employee_id = employee.get("id")
            employee_name = employee.get("name") or "Unknown Employee"
            key = (
                f"id:{employee_id}"
                if employee_id is not None
                else f"name:{employee_name.casefold()}"
            )

            if key not in totals:
                totals[key] = {
                    "id": employee_id,
                    "name": employee_name,
                    "total_tip_cents": 0,
                }

            totals[key]["total_tip_cents"] += max(0, per_employee_cents)

    return list(totals.values())


def _store_tip_setting(session, organization_id: int, store_id: str):
    return session.execute(select(TipStoreSettings).where(
        TipStoreSettings.organization_id == organization_id,
        TipStoreSettings.store_id == store_id.lower(),
    )).scalar_one_or_none()


def _validate_tip_submission_window(setting, report_start: datetime, role: str | None) -> None:
    if setting is None or setting.tip_allocation_start_at is None:
        raise HTTPException(409, "A manager must activate tip allocation in Tip Settings first.")
    if report_start < as_utc(setting.tip_allocation_start_at):
        raise HTTPException(403, "This report begins before this store's tip allocation start.")
    if not role_can_view_payroll_reports(role):
        deadline = report_start + timedelta(hours=setting.employee_submission_hours)
        if datetime.now(timezone.utc) > deadline:
            raise HTTPException(403, "The employee tip submission window has closed. Contact management.")


def _active_store_order(order: dict, store_id: str) -> bool:
    context = order.get("context") or {}
    order_store = context.get("storeId")
    if not order_store:
        ids = {(tx.get("context") or {}).get("storeId") for tx in order.get("transactions") or []}
        ids.discard(None)
        if len(ids) != 1:
            return False
        order_store = next(iter(ids))
    return order_store.lower() == store_id.lower()


def _allocation_orders(orders: list[dict], ranges: list[dict]) -> tuple[list[dict], dict[int, int]]:
    """Validate ranges and calculate claimed orders and employee cents on the server."""
    parsed = []
    for item in ranges:
        try:
            start = _parse_tip_submission_datetime(item["start"])
            end = _parse_tip_submission_datetime(item["end"])
            employee_ids = [int(employee["id"]) for employee in item["employees"]]
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(400, "Invalid tip range or employee.") from exc
        if start >= end or not employee_ids or len(employee_ids) != len(set(employee_ids)):
            raise HTTPException(400, "Invalid or duplicate employees in tip range.")
        parsed.append((start, end, employee_ids, item))
    if [item[0] for item in parsed] != sorted(item[0] for item in parsed):
        raise HTTPException(400, "Tip ranges must be in time order.")
    if any(parsed[i][1] > parsed[i + 1][0] for i in range(len(parsed) - 1)):
        raise HTTPException(400, "Tip ranges may not overlap.")

    claims = []
    totals: dict[int, int] = {}
    seen = set()
    range_totals = [0] * len(parsed)
    for order in orders:
        order_id = order.get("id")
        created = order.get("createdAt")
        if not order_id or not created:
            continue
        moment = _parse_tip_submission_datetime(created)
        matches = [index for index, item in enumerate(parsed)
                   if item[0] <= moment < item[1] or
                   (index == len(parsed) - 1 and moment == item[1])]
        if not matches:
            continue
        if order_id in seen:
            raise HTTPException(409, "Poynt returned a duplicate order ID.")
        seen.add(order_id)
        tip = int(((order.get("amounts") or {}).get("capturedTotals") or {}).get("tipAmount") or 0)
        if tip <= 0:
            continue
        range_totals[matches[0]] += tip
        claims.append((order, tip))
    for index, (_, _, employees, item) in enumerate(parsed):
        try:
            displayed_cents = int(item.get("total_tip_cents", -1))
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "Invalid displayed tip total.") from exc
        if displayed_cents != range_totals[index]:
            raise HTTPException(409, "Tip amounts changed. Refresh the orders report and recalculate.")
        share, remainder = divmod(range_totals[index], len(employees))
        for index, employee_id in enumerate(employees):
            totals[employee_id] = totals.get(employee_id, 0) + share + (index < remainder)
    return claims, {employee_id: cents for employee_id, cents in totals.items() if cents > 0}


def _tip_submission_display(submission: TipSubmission, payouts: list[TipEmployeePayout] | None = None,
                            allowed_payout_ids: set[int] | None = None, store_timezone: str = "UTC") -> dict:
    try:
        data = json.loads(submission.submission_data)
    except (TypeError, ValueError):
        data = {}

    ranges = data.get("ranges", [])
    if not isinstance(ranges, list):
        ranges = []

    return {
        "id": submission.id,
        "store_name": submission.store_name,
        "store_timezone": store_timezone,
        "report_start_at": utc_iso(submission.report_start_at),
        "report_end_at": utc_iso(submission.report_end_at),
        "total_tip_cents": submission.total_tip_cents,
        "payout_method": submission.payout_method,
        "processing_status": submission.processing_status,
        "processed_at": (
            utc_iso(submission.processed_at)
            if submission.processed_at
            else None
        ),
        "submitted_at": utc_iso(submission.submitted_at),
        "ranges": ranges,
        "employee_totals": _aggregate_tip_allocations(ranges),
        "payouts": [{
            "id": payout.id,
            "employee_name": payout.employee_name,
            "employee_id": payout.employee_id,
            "amount_cents": payout.amount_cents,
            "method": payout.payout_method,
            "status": payout.status,
            "paid_at": utc_iso(payout.paid_at),
            "can_confirm": payout.id in (allowed_payout_ids or set()),
        } for payout in (payouts or [])],
        "employee_names": [
            employee.get("name", "Unknown Employee")
            for tip_range in ranges
            if isinstance(tip_range, dict)
            for employee in (tip_range.get("employees") or [])
            if isinstance(employee, dict)
        ],
    }


def _tip_range_totals(submissions: list[dict], own_ids: set[int] | None = None) -> list[dict]:
    """Summarize non-rejected allocations using integer cents and employee IDs."""
    totals = {}
    for submission in submissions:
        rows = submission["payouts"]
        if not rows:
            rows = [{"employee_id": employee["id"], "employee_name": employee["name"],
                     "amount_cents": employee["total_tip_cents"],
                     "method": submission["payout_method"],
                     "status": submission["processing_status"]}
                    for employee in submission["employee_totals"]]
        for row in rows:
            if row["status"] == "rejected" or row["method"] not in {"cash", "paycheck"}:
                continue
            if own_ids is not None and row["employee_id"] not in own_ids:
                continue
            key = ("id", row["employee_id"]) if row["employee_id"] is not None else ("name", row["employee_name"].casefold())
            if key not in totals:
                totals[key] = {"name": row["employee_name"], "cash_cents": 0,
                               "cash_paid_cents": 0, "cash_pending_cents": 0,
                               "paycheck_cents": 0, "paycheck_paid_cents": 0,
                               "paycheck_pending_cents": 0}
            amount = row["amount_cents"]
            totals[key][row["method"] + "_cents"] += amount
            totals[key][row["method"] + ("_paid_cents" if row["status"] == "paid" else "_pending_cents")] += amount
    return sorted(totals.values(), key=lambda row: row["name"].casefold())


def _tip_report_redirect(store_id: str, start: str = "", end: str = "",
                         payment: str = "", status: str = "") -> RedirectResponse:
    params = {"store_id": store_id}
    if start:
        params["start"] = start
    if end:
        params["end"] = end
    if payment in {"cash", "paycheck"}:
        params["payment"] = payment
    if status in {"pending", "paid", "rejected"}:
        params["status"] = status
    return RedirectResponse(f"/poynt/tip-submissions?{urlencode(params)}", status_code=303)


@router.get("/poynt/tip-submissions", response_class=HTMLResponse)
async def tip_submission_report(
    request: Request,
    start: str = "",
    end: str = "",
    store_id: str = "",
    payment: str = "",
    status: str = "",
):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)

    organization_id = get_current_organization_id(request)
    if organization_id is None:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    # Employees may review historical dates, with their data scoped below.
    role = get_organization_role(user_id, organization_id)
    can_view_payroll = role_can_view_payroll_reports(role)
    can_select_date_range = True
    with SessionLocal() as session:
        configured_stores = session.execute(select(OrganizationStore).where(
            OrganizationStore.organization_id == organization_id,
            OrganizationStore.is_active.is_(True),
        )).scalars().all()
    store_map = {store.store_id: store for store in configured_stores if store.timezone_name}
    if not store_map:
        raise HTTPException(409, "Configure a store timezone in Store Settings first.")
    selected_store_id = store_id.lower() if store_id else sorted(store_map)[0]
    if selected_store_id not in store_map:
        raise HTTPException(400, "Select a configured store from this organization.")
    store_timezone = store_map[selected_store_id].timezone_name
    today = datetime.now(ZoneInfo(store_timezone)).date().isoformat()

    # Selectable inclusive store-local date range; default to today.
    start_date = start or today
    end_date = end or today
    bounds = _get_tip_report_date_bounds(start_date, end_date, store_timezone)
    if bounds is None:
        start_date = today
        end_date = today
        bounds = _get_tip_report_date_bounds(start_date, end_date, store_timezone)
        validation_message = "The selected date range was invalid, so today's submissions are shown."
    else:
        validation_message = ""

    day_start, day_end, _, _ = bounds

    selected_payment = payment if payment in {"cash", "paycheck"} else ""
    selected_status = status if status in {"pending", "paid", "rejected"} else ""

    submission_query = select(TipSubmission).where(
        TipSubmission.organization_id == organization_id,
        TipSubmission.store_id == selected_store_id,
        TipSubmission.submitted_at >= day_start,
        TipSubmission.submitted_at < day_end,
    )

    submission_query = submission_query.order_by(
        TipSubmission.submitted_at.desc(),
        TipSubmission.id.desc(),
    )

    with SessionLocal() as session:
        submissions = session.execute(submission_query).scalars().all()
        payout_rows = session.execute(select(TipEmployeePayout).where(
            TipEmployeePayout.submission_id.in_([item.id for item in submissions])
        )).scalars().all() if submissions else []
        payout_map = {}
        for payout in payout_rows:
            payout_map.setdefault(payout.submission_id, []).append(payout)
        store_ids = {item.store_id for item in submissions}
        settings = session.execute(select(TipStoreSettings).where(
            TipStoreSettings.organization_id == organization_id,
            TipStoreSettings.store_id.in_(store_ids),
        )).scalars().all() if store_ids else []
        settings_map = {setting.store_id: setting for setting in settings}
        linked_employees = session.execute(select(Employee.id).where(
            Employee.organization_id == organization_id, Employee.user_id == user_id,
        )).scalars().all()
        own_ids = set(linked_employees)
        allowed_ids = set()
        for submission in submissions:
            setting = settings_map.get(submission.store_id)
            for payout in payout_map.get(submission.id, []):
                if payout.payout_method == "paycheck":
                    permitted = role_can_view_payroll_reports(role)
                else:
                    permitted = (role_can_manage_organization(role) or
                                 bool(setting and setting.cash_confirmer_user_id == user_id) or
                                 bool(setting and setting.cash_confirmation == "self" and payout.employee_id in own_ids))
                if permitted:
                    allowed_ids.add(payout.id)

        range_display = [_tip_submission_display(item, payout_map.get(item.id), allowed_ids, store_timezone)
                         for item in submissions]
        range_totals = _tip_range_totals(range_display, None if can_view_payroll else own_ids)

        # New submissions filter by each employee's payout. Legacy submissions
        # retain their submission-level payment and processing filters.
        if selected_payment or selected_status:
            submissions = [item for item in submissions if (
                any((not selected_payment or row.payout_method == selected_payment) and
                    (not selected_status or row.status == selected_status)
                    for row in payout_map.get(item.id, []))
                if payout_map.get(item.id) else
                (not selected_payment or item.payout_method == selected_payment) and
                (not selected_status or item.processing_status == selected_status)
            )]

        display = []
        for item in submissions:
            visible_payouts = payout_map.get(item.id)
            if visible_payouts and (selected_payment or selected_status):
                visible_payouts = [row for row in visible_payouts if
                    (not selected_payment or row.payout_method == selected_payment) and
                    (not selected_status or row.status == selected_status)]
            shown = _tip_submission_display(item, visible_payouts, allowed_ids, store_timezone)
            if not can_view_payroll:
                # Preserve cash-confirmer access; other historical rows are personal.
                if shown["payouts"]:
                    shown["payouts"] = [row for row in shown["payouts"]
                                        if row["employee_id"] in own_ids or row["id"] in allowed_ids]
                    if not shown["payouts"]:
                        continue
                    shown["employee_totals"] = []
                    shown["total_tip_cents"] = sum(row["amount_cents"] for row in shown["payouts"])
                else:
                    shown["employee_totals"] = [row for row in shown["employee_totals"] if row["id"] in own_ids]
                    if not shown["employee_totals"]:
                        continue
                    shown["total_tip_cents"] = sum(row["total_tip_cents"] for row in shown["employee_totals"])
                shown["ranges"] = []
                shown["employee_names"] = []
            display.append(shown)

    return templates.TemplateResponse(
        request=request,
        name="tip_submissions.html",
        context={
            "submissions": display,
            "tip_range_totals": range_totals,
            "tip_report_start_date": start_date,
            "tip_report_end_date": end_date,
            "tip_report_can_select_date_range": can_select_date_range,
            "tip_report_validation_message": validation_message,
            "tip_report_payment": selected_payment,
            "tip_report_status": selected_status,
            "tip_report_can_process": can_view_payroll,
            "tip_report_role": role,
            "tip_report_store_id": selected_store_id,
            "tip_report_store_timezone": store_timezone,
            "tip_report_stores": [{"id": key, "name": value.display_name or value.poynt_name}
                                  for key, value in sorted(store_map.items())],
        },
    )


@router.post("/poynt/tip-submissions/{submission_id}/status")
async def update_tip_submission_status(
    submission_id: int,
    request: Request,
    processing_status: str = Form(...),
    return_start: str = Form(""),
    return_end: str = Form(""),
    return_store_id: str = Form(""),
    return_payment: str = Form(""),
    return_status: str = Form(""),
):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)

    organization_id = get_current_organization_id(request)
    if organization_id is None:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    role = get_organization_role(user_id, organization_id)
    if not role_can_view_payroll_reports(role):
        raise HTTPException(status_code=403, detail="Payroll access is required.")

    if processing_status not in {"paid", "rejected"}:
        raise HTTPException(status_code=400, detail="Invalid tip submission status.")

    with SessionLocal() as session:
        submission = session.execute(
            select(TipSubmission).where(
                TipSubmission.id == submission_id,
                TipSubmission.organization_id == organization_id,
            )
        ).scalar_one_or_none()

        if submission is None:
            raise HTTPException(status_code=404, detail="Tip submission not found.")

        new_payouts = session.execute(select(TipEmployeePayout.id).where(
            TipEmployeePayout.submission_id == submission.id
        )).first()
        if new_payouts:
            raise HTTPException(409, "Use employee payout actions for this submission.")

        submission.processing_status = processing_status
        submission.processed_at = datetime.now(timezone.utc)
        submission.processed_by_user_id = user_id
        session.commit()

    return_params = {}
    if return_store_id:
        return_params["store_id"] = return_store_id
    if return_start:
        return_params["start"] = return_start
    if return_end:
        return_params["end"] = return_end
    if return_payment in {"cash", "paycheck"}:
        return_params["payment"] = return_payment
    if return_status in {"pending", "paid", "rejected"}:
        return_params["status"] = return_status

    redirect_url = "/poynt/tip-submissions"
    if return_params:
        redirect_url = f"{redirect_url}?{urlencode(return_params)}"

    return RedirectResponse(redirect_url, status_code=303)


@router.post("/poynt/tip-payouts/{payout_id}/paid")
async def mark_tip_payout_paid(
    payout_id: int, request: Request, return_start: str = Form(""),
    return_end: str = Form(""), return_payment: str = Form(""), return_status: str = Form(""),
):
    user_id = request.session.get("user_id")
    organization_id = get_current_organization_id(request)
    if not user_id or organization_id is None:
        raise HTTPException(401, "Sign in to confirm a payout.")
    role = get_organization_role(user_id, organization_id)
    with SessionLocal() as session:
        row = session.execute(select(TipEmployeePayout, TipSubmission).join(
            TipSubmission, TipSubmission.id == TipEmployeePayout.submission_id
        ).where(TipEmployeePayout.id == payout_id,
                TipSubmission.organization_id == organization_id).with_for_update()).first()
        if not row:
            raise HTTPException(404, "Payout not found.")
        payout, submission = row
        # Keep redirect data available after commit expires the ORM instance.
        redirect_store_id = submission.store_id
        if payout.status != "pending" or submission.processing_status == "rejected":
            raise HTTPException(409, "This payout is not pending.")
        setting = _store_tip_setting(session, organization_id, submission.store_id)
        if payout.payout_method == "paycheck":
            if not role_can_view_payroll_reports(role):
                raise HTTPException(403, "Payroll permission is required.")
            mode = "payroll"
        else:
            designated = bool(setting and setting.cash_confirmer_user_id == user_id)
            authorized = role_can_manage_organization(role) or designated
            employee = session.get(Employee, payout.employee_id)
            self_confirm = (setting and setting.cash_confirmation == "self" and
                            employee and employee.organization_id == organization_id and
                            employee.user_id == user_id)
            if not (authorized or self_confirm):
                raise HTTPException(403, "Cash confirmation permission is required.")
            mode = "self" if self_confirm and not authorized else "authorized"
        payout.status = "paid"
        payout.paid_at = datetime.now(timezone.utc)
        payout.paid_by_user_id = user_id
        payout.confirmation_mode = mode
        session.flush()
        statuses = session.execute(select(TipEmployeePayout.status).where(
            TipEmployeePayout.submission_id == submission.id
        )).scalars().all()
        if all(status == "paid" for status in statuses):
            submission.processing_status = "paid"
            submission.processed_at = datetime.now(timezone.utc)
            submission.processed_by_user_id = user_id
        session.commit()
    return _tip_report_redirect(redirect_store_id, return_start, return_end, return_payment, return_status)


@router.get("/poynt/tip-settings", response_class=HTMLResponse)
async def tip_store_settings_page(request: Request, store_id: str):
    user_id = request.session.get("user_id")
    organization_id = get_current_organization_id(request)
    if not user_id or organization_id is None or not role_can_manage_organization(
        get_organization_role(user_id, organization_id)
    ):
        raise HTTPException(403, "Manager permission is required.")
    with SessionLocal() as session:
        store = session.execute(select(OrganizationStore).where(
            OrganizationStore.organization_id == organization_id,
            OrganizationStore.store_id == store_id.lower(),
            OrganizationStore.is_active.is_(True),
        )).scalar_one_or_none()
        if store is None or not store.timezone_name:
            raise HTTPException(409, "Configure this store and its timezone in Store Settings first.")
        store_timezone = ZoneInfo(store.timezone_name)
        setting = _store_tip_setting(session, organization_id, store_id)
        members = session.execute(select(User).join(
            OrganizationMember, OrganizationMember.user_id == User.id
        ).where(OrganizationMember.organization_id == organization_id)).scalars().all()
        values = {
            "payout_policy": setting.payout_policy if setting else "choice",
            "cash_confirmation": setting.cash_confirmation if setting else "authorized",
            "cash_confirmer_user_id": setting.cash_confirmer_user_id if setting else None,
            "employee_submission_hours": setting.employee_submission_hours if setting else 24,
            "tip_allocation_start_at": (
                as_utc(setting.tip_allocation_start_at)
                .astimezone(store_timezone).strftime("%Y-%m-%dT%H:%M")
                if setting and setting.tip_allocation_start_at else ""
            ),
        }
        choices = [{"id": m.id, "name": f"{m.first_name or ''} {m.last_name or ''}".strip() or m.email}
                   for m in members]
    return templates.TemplateResponse(request=request, name="tip_settings.html", context={
        "store_id": store_id.lower(), "settings": values, "members": choices,
        "suggested_start_at": datetime.now(store_timezone).strftime("%Y-%m-%dT00:00"),
        "store_timezone": store.timezone_name,
    })


@router.post("/poynt/tip-settings")
async def save_tip_store_settings(
    request: Request, store_id: str = Form(...), payout_policy: str = Form(...),
    cash_confirmation: str = Form(...), cash_confirmer_user_id: str = Form(""),
    employee_submission_hours: int = Form(24), tip_allocation_start_at: str = Form(""),
):
    user_id = request.session.get("user_id")
    organization_id = get_current_organization_id(request)
    if not user_id or organization_id is None or not role_can_manage_organization(
        get_organization_role(user_id, organization_id)
    ):
        raise HTTPException(403, "Manager permission is required.")
    if payout_policy not in {"choice", "cash", "paycheck"} or cash_confirmation not in {"self", "authorized"}:
        raise HTTPException(400, "Invalid tip settings.")
    if not store_id or len(store_id) > 100:
        raise HTTPException(400, "Invalid store ID.")
    if not 1 <= employee_submission_hours <= 720:
        raise HTTPException(400, "Employee submission window must be between 1 and 720 hours.")
    with SessionLocal() as session:
        store = session.execute(select(OrganizationStore).where(
            OrganizationStore.organization_id == organization_id,
            OrganizationStore.store_id == store_id.lower(),
            OrganizationStore.is_active.is_(True),
        )).scalar_one_or_none()
        if store is None or not store.timezone_name:
            raise HTTPException(409, "Configure this store and its timezone in Store Settings first.")
        confirmer_id = None
        if cash_confirmer_user_id:
            try:
                confirmer_id = int(cash_confirmer_user_id)
            except ValueError as exc:
                raise HTTPException(400, "Invalid confirmer.") from exc
            member = session.execute(select(OrganizationMember.id).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == confirmer_id,
            )).first()
            if not member:
                raise HTTPException(400, "Confirmer must belong to this organization.")
        setting = _store_tip_setting(session, organization_id, store_id)
        if setting is None:
            setting = TipStoreSettings(organization_id=organization_id, store_id=store_id.lower())
            session.add(setting)
        if setting.tip_allocation_start_at is None:
            try:
                local_start = datetime.fromisoformat(tip_allocation_start_at)
                if local_start.tzinfo is not None:
                    raise ValueError("Expected local time")
                setting.tip_allocation_start_at = local_to_utc(local_start, ZoneInfo(store.timezone_name))
            except ValueError as exc:
                raise HTTPException(400, "Choose a valid tip allocation start date and time.") from exc
        setting.payout_policy = payout_policy
        setting.cash_confirmation = cash_confirmation
        setting.cash_confirmer_user_id = confirmer_id
        setting.employee_submission_hours = employee_submission_hours
        session.commit()
    return RedirectResponse(f"/poynt/tip-settings?{urlencode({'store_id': store_id.lower()})}", status_code=303)


@router.post("/poynt/tip-submissions/{submission_id}/reject")
async def reject_new_tip_submission(
    submission_id: int, request: Request, return_start: str = Form(""),
    return_end: str = Form(""), return_payment: str = Form(""), return_status: str = Form(""),
):
    user_id = request.session.get("user_id")
    organization_id = get_current_organization_id(request)
    if not user_id or organization_id is None:
        raise HTTPException(401, "Sign in first.")
    if not role_can_manage_organization(get_organization_role(user_id, organization_id)):
        raise HTTPException(403, "Manager permission is required.")
    with SessionLocal() as session:
        submission = session.execute(select(TipSubmission).where(
            TipSubmission.id == submission_id,
            TipSubmission.organization_id == organization_id,
        ).with_for_update()).scalar_one_or_none()
        if not submission:
            raise HTTPException(404, "Submission not found.")
        payouts = session.execute(select(TipEmployeePayout).where(
            TipEmployeePayout.submission_id == submission_id
        )).scalars().all()
        if not payouts or any(p.status != "pending" for p in payouts):
            raise HTTPException(409, "Only fully unpaid submissions can be rejected.")
        # Keep redirect data available after commit expires the ORM instance.
        redirect_store_id = submission.store_id
        for payout in payouts:
            payout.status = "rejected"
        # The rejected record stays visible; its order IDs become available for correction.
        for claim in session.execute(select(TipOrderClaim).where(
            TipOrderClaim.submission_id == submission_id
        )).scalars():
            session.delete(claim)
        submission.processing_status = "rejected"
        submission.processed_at = datetime.now(timezone.utc)
        submission.processed_by_user_id = user_id
        session.commit()
    return _tip_report_redirect(redirect_store_id, return_start, return_end, return_payment, return_status)


@router.post("/poynt/tip-submissions")
async def submit_tip_record(
    request: Request,
    store_id: str = Form(...),
    store_name: str = Form(...),
    report_start_at: str = Form(...),
    report_end_at: str = Form(...),
    total_tip_cents: int = Form(...),
    payout_choices: str = Form("{}"),
    submission_json: str = Form(...),
):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)

    organization_id = get_current_organization_id(request)
    if organization_id is None:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    try:
        report_start = _parse_tip_submission_datetime(report_start_at)
        report_end = _parse_tip_submission_datetime(report_end_at)
        submission_data = json.loads(submission_json)
        choices = json.loads(payout_choices)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(400, "Invalid tip submission.")

    if report_start >= report_end or report_end - report_start > timedelta(days=3):
        raise HTTPException(400, "Invalid report range.")

    if not isinstance(submission_data, dict) or not isinstance(
        submission_data.get("ranges"), list
    ) or not submission_data.get("ranges"):
        raise HTTPException(400, "At least one tip range is required.")

    if len(submission_data["ranges"]) > 6:
        raise HTTPException(400, "Too many tip ranges.")

    for tip_range in submission_data["ranges"]:
        if not isinstance(tip_range, dict):
            raise HTTPException(400, "Invalid tip range.")
        if not isinstance(tip_range.get("employees"), list) or not tip_range["employees"]:
            raise HTTPException(400, "A tip range has no employees.")

    if not isinstance(choices, dict) or not store_id or len(store_id) > 100:
        raise HTTPException(400, "Invalid store or payout choices.")
    store_id = store_id.lower()
    for tip_range in submission_data["ranges"]:
        try:
            range_start = _parse_tip_submission_datetime(tip_range["start"])
            range_end = _parse_tip_submission_datetime(tip_range["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(400, "Invalid tip range date.") from exc
        if range_start < report_start or range_end > report_end:
            raise HTTPException(400, "A tip range is outside the report period.")

    role = get_organization_role(user_id, organization_id)
    with SessionLocal() as session:
        store = session.execute(select(OrganizationStore).where(
            OrganizationStore.organization_id == organization_id,
            OrganizationStore.store_id == store_id,
            OrganizationStore.is_active.is_(True),
        )).scalar_one_or_none()
        if store is None or not store.timezone_name:
            raise HTTPException(409, "Configure this store's timezone before submitting tips.")
        _validate_tip_submission_window(
            _store_tip_setting(session, organization_id, store_id), report_start, role
        )

    credentials = get_poynt_credentials(organization_id)
    if credentials is None:
        raise HTTPException(409, "Connect Poynt before submitting tips.")
    try:
        orders = await fetch_poynt_orders(
            credentials, organization_id,
            report_start.isoformat(),
            report_end.isoformat(),
        )
    except Exception as exc:
        logger.exception("Could not refresh Poynt orders for tip submission")
        raise HTTPException(502, "Could not verify current Poynt tips.") from exc
    orders = [order for order in filter_completed_orders(orders)[0]
              if _active_store_order(order, store_id)]
    claims, allocations = _allocation_orders(orders, submission_data["ranges"])
    if not claims or sum(tip for _, tip in claims) != total_tip_cents:
        raise HTTPException(409, "Tip total changed. Refresh the report and recalculate.")

    submitted_at = datetime.now(timezone.utc)

    with SessionLocal() as session:
        employee_ids = list(allocations)
        employees = session.execute(select(Employee).where(
            Employee.organization_id == organization_id,
            Employee.is_active.is_(True),
            Employee.id.in_(employee_ids),
        )).scalars().all()
        if len(employees) != len(employee_ids):
            raise HTTPException(400, "An employee is inactive or belongs to another organization.")
        setting = _store_tip_setting(session, organization_id, store_id)
        _validate_tip_submission_window(setting, report_start, role)
        policy = setting.payout_policy if setting else "choice"
        if policy not in {"cash", "paycheck", "choice"}:
            raise HTTPException(400, "Invalid store payout policy.")
        methods = {}
        for employee in employees:
            method = choices.get(str(employee.id), "paycheck") if policy == "choice" else policy
            if method not in {"cash", "paycheck"}:
                raise HTTPException(400, "Invalid employee payout method.")
            methods[employee.id] = method
        submission = TipSubmission(
            organization_id=organization_id,
            submitted_by_user_id=user_id,
            store_id=store_id,
            store_name=store_name,
            report_start_at=report_start,
            report_end_at=report_end,
            total_tip_cents=max(0, int(total_tip_cents)),
            payout_method=(next(iter(set(methods.values()))) if len(set(methods.values())) == 1 else "mixed"),
            processing_status="pending",
            submission_data=json.dumps(submission_data),
            submitted_at=submitted_at,
        )
        session.add(submission)
        session.flush()
        for order, tip in claims:
            session.add(TipOrderClaim(
                organization_id=organization_id,
                submission_id=submission.id,
                poynt_business_id=credentials.business_id,
                poynt_order_id=order["id"],
                store_id=store_id,
                created_at=_parse_tip_submission_datetime(order["createdAt"]),
                tip_cents=tip,
            ))
        for employee in employees:
            session.add(TipEmployeePayout(
                submission_id=submission.id,
                employee_id=employee.id,
                employee_name=f"{employee.first_name} {employee.last_name}".strip(),
                amount_cents=allocations[employee.id],
                payout_method=methods[employee.id],
                status="pending",
            ))
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(409, "One or more orders have already been allocated. Refresh the report.") from exc

    return RedirectResponse(f"/poynt/tip-submissions?{urlencode({'store_id': store_id})}", status_code=303)

@router.get("/poynt/orders", response_class=HTMLResponse)
async def poynt_orders(
    request: Request,
    start: str = "",
    end: str = "",
    stores: list[str] | None = Query(default=None),    
):
    user_id = request.session.get("user_id")
    logger.info("filtering stores to: %s", stores)


    if not user_id:
        return RedirectResponse(
            "/login",
            status_code=303
        )

    organization_id = get_current_organization_id(request)

    if organization_id is None:
        request.session.clear()
        return RedirectResponse(
            "/login",
            status_code=303
        )

    with SessionLocal() as session:
        configured_stores = session.execute(select(OrganizationStore).where(
            OrganizationStore.organization_id == organization_id,
            OrganizationStore.is_active.is_(True),
        )).scalars().all()
    configured = {store.store_id: store for store in configured_stores if store.timezone_name}
    store_names = {store_id: store.display_name or store.poynt_name for store_id, store in configured.items()}
    available_stores = [{"id": store_id, "name": name} for store_id, name in sorted(store_names.items(), key=lambda item: item[1].lower())]
    requested_stores = {store_id.lower() for store_id in (stores or []) if store_id}
    if requested_stores and not requested_stores.issubset(configured):
        raise HTTPException(400, "Select configured stores from this organization.")
    selected_store_ids = sorted(requested_stores or configured)
    selected_zones = {configured[store_id].timezone_name for store_id in selected_store_ids}
    store_timezone = next(iter(selected_zones)) if len(selected_zones) == 1 else None
    if not configured or not store_timezone:
        message = ("Configure at least one store and its timezone in Store Settings."
                   if not configured else "Select stores in one timezone for this report.")
        return templates.TemplateResponse(request=request, name="orders.html", context={
            "report_generated": False, "validation_title": "Store Selection Required",
            "validation_message": message, "available_stores": available_stores,
            "selected_stores": selected_store_ids, "start_input_value": start,
            "end_input_value": end, "store_timezone": "", "chart_data_json": "[]",
            "item_flow_json": "[]", "revenue_flow_json": "[]",
        })

    if not start and not end:
        return templates.TemplateResponse(
            request=request,
            name="orders.html",
            context={
                "report_generated": False,
                "validation_title": "",
                "validation_message": "Enter a start and end date and time to generate order metrics.",
                "start_input_value": "",
                "end_input_value": "",
                "available_stores": available_stores,
                "selected_stores": selected_store_ids,
                "store_timezone": store_timezone,
                "chart_data_json": "[]",
                "item_flow_json": "[]",
                "revenue_flow_json": "[]",

                # Tip Calculator defaults
                "tip_calculator_data": [],
                "tip_calculator_enabled": False,
                "tip_calculator_store_name": "",
                "tip_calculator_store_id": "",
                "start_at_for_tip_calculator": None,
                "end_at_for_tip_calculator": None,
            },
        )
    order_date_params = get_orders_date_range(start, end, store_timezone)

    if "error_title" in order_date_params:
        return templates.TemplateResponse(
            request=request,
            name="orders.html",
            context={
                "report_generated": False,
                "validation_title": order_date_params["error_title"],
                "validation_message": order_date_params["error_message"],
                "start_input_value": start,
                "end_input_value": end,
                "available_stores": available_stores,
                "selected_stores": selected_store_ids,
                "store_timezone": store_timezone,
                "chart_data_json": "[]",
                "item_flow_json": "[]",
                "revenue_flow_json": "[]",
            },
        )

    start_at = order_date_params["start_at"]
    end_at = order_date_params["end_at"] 

    report_span_seconds = order_date_params['span_seconds'] 

    # preserve inputs    
    start_input_value = start
    end_input_value = end    

    credentials = get_poynt_credentials(organization_id)

    logger.info(
        "Poynt orders credentials: found=%s",
        credentials is not None,
    )       

    if not credentials:
        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "Poynt Error",
                "paragraphs": [
                    "No Poynt connection was found."
                ],
                "show_dashboard_link": True,
            },
            status_code=404,
        )

    try:
        logger.info(
            "Poynt order query: startAt=%s, endAt=%s",
            start_at,
            end_at,
        )        

        orders = await fetch_poynt_orders(
            credentials,
            organization_id,
            start_at,
            end_at,
        )

        orders = filter_orders_by_created_at(
            orders,
            start_at,
            end_at,
        )        


        if not orders:
            logger.info(
                "No Poynt orders found for requested date range: %s - %s",
                start_at,
                end_at,
            )

            return templates.TemplateResponse(
                request=request,
                name="orders.html",
                context={
                    "report_generated": False,
                    "validation_title": "No Orders Found",
                    "validation_message": (
                        "No orders were found for the selected date range."
                    ),
                    "start_input_value": start,
                    "end_input_value": end,
                    "available_stores": available_stores,
                    "selected_stores": selected_store_ids,
                    "store_timezone": store_timezone,
                    "chart_data_json": "[]",
                    "item_flow_json": "[]",
                    "revenue_flow_json": "[]",

                    # Tip Calculator defaults
                    "tip_calculator_data": [],
                    "tip_calculator_enabled": False,
                    "tip_calculator_store_name": "",
                    "start_at_for_tip_calculator": None,
                    "end_at_for_tip_calculator": None,
                },
            )
        
        available_store_ids = get_available_store_ids(orders, store_names)

        if not available_store_ids:
            return templates.TemplateResponse(
                request=request,
                name="orders.html",
                context={
                    "report_generated": False,
                    "validation_title": "No Store Data",
                    "validation_message": "No store information was found in the orders for this date range.",
                    "start_input_value": start,
                    "end_input_value": end,
                    "available_stores": available_stores,
                    "selected_stores": selected_store_ids,
                    "store_timezone": store_timezone,
                    "chart_data_json": "[]",
                    "item_flow_json": "[]",
                    "revenue_flow_json": "[]",
                },
            )

        orders = filter_orders_by_stores(
            orders,
            selected_store_ids,
        )
        if not orders:
            return templates.TemplateResponse(request=request, name="orders.html", context={
                "report_generated": False, "validation_title": "No Orders Found",
                "validation_message": "No orders were found for the selected stores and time range.",
                "start_input_value": start, "end_input_value": end,
                "available_stores": available_stores, "selected_stores": selected_store_ids,
                "store_timezone": store_timezone, "chart_data_json": "[]",
                "item_flow_json": "[]", "revenue_flow_json": "[]",
            })

    except PoyntReauthorizationRequired:
        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "Poynt Authorization Required",
                "paragraphs": [
                    "Your Poynt authorization has expired.",
                    "Please reconnect your Poynt account.",
                ],
                "show_dashboard_link": True,
            },
            status_code=401,
        )

    except Exception as e:
        logger.error(
            "Poynt recent orders request failed: %s",
            e,
        )

        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "Poynt Orders Error",
                "paragraphs": [
                    "The recent orders request failed.",
                    "Check the application logs.",
                ],
                "show_dashboard_link": True,
            },
            status_code=502,
        )


    summary_text = f"{len(orders)}"

    # Newest first.
    orders = sorted(
        orders,
        key=lambda order: order.get("createdAt", ""),
        reverse=True,
    )

    orders, cancelled_order_count = filter_completed_orders(orders)                

    metrics = calculate_order_metrics(orders)

    total_revenue = metrics["total_revenue"]
    total_items = metrics["total_items"]
    total_tips = metrics["total_tips"]

    oldest_order_at = metrics["oldest_order_at"]
    newest_order_at = metrics["newest_order_at"]

    order_span_seconds = metrics["order_span_seconds"]
    average_seconds_between_orders = metrics[
        "average_seconds_between_orders"
    ]
    average_seconds_between_items = metrics[
        "average_seconds_between_items"
    ]

    items_per_order = metrics["items_per_order"]
    tip_ratio = metrics["tip_ratio"]

    chart_data = prepare_chart_data(orders)

    chart_data_json = chart_data["order_intervals_json"]
    item_flow_json = chart_data["item_flow_json"]
    revenue_flow_json = chart_data["revenue_flow_json"]

    fastest_processing = get_fastest_processing_times(orders)

    if total_items.is_integer():
        total_items_display = str(int(total_items))
    else:
        total_items_display = str(total_items)
    # END Insert

    if isinstance(items_per_order, float):
        items_per_order_display = f"{items_per_order:,.2f}"
    else:
        logger.debug("items_per_order %s isn't a float", items_per_order)    
        items_per_order_display = "-"

    if isinstance(tip_ratio, float):    
        tip_ratio_display = f"{tip_ratio:,.1%}"
    else:
        logger.debug(" %s isn't a float", tip_ratio)    
        tip_ratio_display = "-"        

    total_revenue_display = f"${total_revenue:,.2f}"
    total_tips_display = f"${total_tips:,.2f}"
    revenue_per_hour_orders = total_revenue / (order_span_seconds / 3600) if order_span_seconds else 0
    revenue_per_hour_orders_display = f"${revenue_per_hour_orders:,.2f}"  
    revenue_per_hour_range = total_revenue / (report_span_seconds / 3600) if order_span_seconds else 0
    revenue_per_hour_report_display = f"${revenue_per_hour_range:,.2f}"  
    cog_ratio = 0.25
    tax_rate_estimate = 0.083
    profit_rate_per_hour = revenue_per_hour_range * (1 - cog_ratio - tax_rate_estimate)
    profit_per_hour_display = f"${profit_rate_per_hour:,.2f}"   

    fastest_1_item = fastest_processing.get("1")
    fastest_2_item = fastest_processing.get("2")
    fastest_3_item = fastest_processing.get("3")

    fastest_1_item_display = (
        f"{fastest_1_item:.1f}"
        if fastest_1_item is not None
        else "-"
    )

    fastest_2_item_display = (
        f"{fastest_2_item:.1f}"
        if fastest_2_item is not None
        else "-"
    )

    fastest_3_item_display = (
        f"{fastest_3_item:.1f}"
        if fastest_3_item is not None
        else "-"
    )    

    chart_display_flag = ""
    if not len(orders):
        chart_display_flag = "display: none;"    

    if order_span_seconds is not None:
        order_span_time_duration_display = format_duration(
            order_span_seconds
        )
    else:
        order_span_time_duration_display = "-"

    if average_seconds_between_items is not None:
        average_seconds_between_items_display = (
            f"{average_seconds_between_items:.1f}"
        )
    else:
        average_seconds_between_items_display = "-"

    if average_seconds_between_orders is not None:
        average_seconds_display = (
            f"{average_seconds_between_orders:.1f}"
        )
    else:
        average_seconds_display = "-"    

    if oldest_order_at and newest_order_at:
        oldest_order_iso = oldest_order_at.isoformat()
        newest_order_iso = newest_order_at.isoformat()

    else:
        oldest_order_iso = ""
        newest_order_iso = ""
        order_span_display = "Unknown"        

    # Count units ordered by SKU across the displayed orders.
    sku_counts = {}

    prefix_counts = get_prefix_counts(orders, sku_counts)

    sku_rows = get_sku_rows(sku_counts)

    category_rows = get_category_rows(
        sku_prefix_to_category_map,
        prefix_counts,
    )

    orders_data, store_ids = get_orders_data(orders)    

    stores_display = get_stores_display(store_ids, store_names)

    tip_calculator_data = get_tip_calculator_data(orders)

    tip_calculator_employees = get_tip_calculator_employees(
        organization_id
    )

    tip_payout_policy = "choice"
    tip_setting = None
    if len(store_ids) == 1:
        with SessionLocal() as session:
            tip_setting = _store_tip_setting(session, organization_id, next(iter(store_ids)))
            if tip_setting:
                tip_payout_policy = tip_setting.payout_policy

    tip_calculator_enabled = (
        len(store_ids) == 1
        and bool(tip_calculator_employees)
        and tip_setting is not None
        and tip_setting.tip_allocation_start_at is not None
    )

    tip_calculator_store_name = (
        stores_display
        if len(store_ids) == 1
        else ""
    )

    if len(store_ids) != 1:
        tip_calculator_disabled_reason = (
            "Tip Calculator requires exactly one store in the report."
        )
    elif not tip_calculator_employees:
        tip_calculator_disabled_reason = (
            "Add an active employee before using the Tip Calculator."
        )
    elif tip_setting is None or tip_setting.tip_allocation_start_at is None:
        tip_calculator_disabled_reason = "A manager must activate tip allocation in Tip Settings."
    else:
        tip_calculator_disabled_reason = ""

    return templates.TemplateResponse(
        request=request,
        name="orders.html",
        context={
            "report_generated": True,
            "summary_text": summary_text,
            "cancelled_order_count": cancelled_order_count,
            "total_revenue_display": total_revenue_display,
            "total_revenue": total_revenue,
            "total_items_display": total_items_display,
            "items_per_order_display": items_per_order_display,
            "total_tips_display": total_tips_display,
            "tip_ratio_display": tip_ratio_display,
            "average_seconds_display": average_seconds_display,
            "average_seconds_between_items_display": average_seconds_between_items_display,
            "fastest_1_item_display": fastest_1_item_display,
            "fastest_2_item_display": fastest_2_item_display,
            "fastest_3_item_display": fastest_3_item_display,
            "chart_display_flag": chart_display_flag,
            "oldest_order_iso": oldest_order_iso,
            "newest_order_iso": newest_order_iso,
            "order_span_time_duration_display": order_span_time_duration_display,
            "start_input_value": start_input_value,
            "end_input_value": end_input_value,
            "stores_display": stores_display,
            "available_stores": available_stores,
            "selected_stores": selected_store_ids,
            "store_timezone": store_timezone,
            "sku_rows": sku_rows,
            "category_rows": category_rows,
            "orders_data": orders_data,
            "chart_data_json": chart_data_json,
            "item_flow_json": item_flow_json,
            "revenue_flow_json": revenue_flow_json,
            "revenue_per_hour_orders_display": revenue_per_hour_orders_display,
            "revenue_per_hour_report_display": revenue_per_hour_report_display,
            "profit_per_hour_display": profit_per_hour_display,
            "tip_calculator_data": tip_calculator_data,
            "tip_payout_policy": tip_payout_policy,
            "tip_allocation_start_at": (
                utc_iso(tip_setting.tip_allocation_start_at)
                if tip_setting and tip_setting.tip_allocation_start_at else None
            ),
            "tip_employee_submission_hours": tip_setting.employee_submission_hours if tip_setting else 24,
            "tip_employee_window_exempt": role_can_view_payroll_reports(
                get_organization_role(user_id, organization_id)
            ),
            "tip_can_manage_settings": role_can_manage_organization(
                get_organization_role(user_id, organization_id)
            ),
            "tip_calculator_employees": tip_calculator_employees,
            "tip_calculator_enabled": tip_calculator_enabled,
            "tip_calculator_store_name": tip_calculator_store_name,
            "tip_calculator_store_id": next(iter(store_ids), "") if len(store_ids) == 1 else "",
            "tip_calculator_disabled_reason": tip_calculator_disabled_reason,
            "start_at_for_tip_calculator": start_at,
            "end_at_for_tip_calculator": end_at,                  
        },
    )



@router.get("/poynt/stores", response_class=HTMLResponse)
async def poynt_stores(
    request: Request,
    start: str = "",
    end: str = "",
):
    user_id = request.session.get("user_id")

    if not user_id:
        return RedirectResponse(
            "/login",
            status_code=303
        )

    organization_id = get_current_organization_id(request)

    if organization_id is None:
        request.session.clear()
        return RedirectResponse(
            "/login",
            status_code=303
        )

    credentials = get_poynt_credentials(organization_id)

    if not credentials:
        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "Poynt Error",
                "paragraphs": [
                    "No Poynt connection was found."
                ],
                "show_dashboard_link": True,
            },
            status_code=404,
        )

    try:
        client = PoyntClient(
            credentials,
            organization_id=organization_id,
        )

        businesses = await client.get_stores()

    except PoyntReauthorizationRequired:
        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "Poynt Authorization Required",
                "paragraphs": [
                    "Your Poynt authorization has expired.",
                    "Please reconnect your Poynt account.",
                ],
                "show_dashboard_link": True,
            },
            status_code=401,
        )

    except Exception as e:
        print(
            f"Poynt stores request failed: "
            f"{type(e).__name__}: {e}",
            flush=True
        )

        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "Poynt stores Error",
                "paragraphs": [
                    "The stores request failed.",
                    "Check the application logs.",
                ],
                "show_dashboard_link": True,
            },
            status_code=502,
        )

    return templates.TemplateResponse(
        request=request,
        name="message.html",
        context={
            "title": "Poynt Stores Success!",
            "paragraphs": [
                "Stores request succeeded.",
                "The Poynt access token was retrieved from the database and used to make this request.",
            ],
            "show_dashboard_link": True,
        },
    )
    
