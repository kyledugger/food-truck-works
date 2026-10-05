"""Pure, integer-cent store-local reporting. Browser timezone is never used."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from categories import sku_prefix_to_category_map
from sku_map import fix_sku
from store_time import local_day_bounds, utc_iso


def instant(value):
    if not isinstance(value, str):
        raise ValueError("Missing timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp requires an offset")
    return result.astimezone(timezone.utc)


def number(value):
    try:
        result = Decimal(str(value or 0))
        return result if result.is_finite() else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def cents(value):
    return int(number(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def order_store(order):
    primary = (order.get("context") or {}).get("storeId")
    if primary:
        return str(primary)
    stores = {str(t["context"]["storeId"]) for t in order.get("transactions") or []
              if (t.get("context") or {}).get("storeId")}
    return next(iter(stores)) if len(stores) == 1 else None


def money(order):
    amounts = order.get("amounts") or {}
    captured = amounts.get("capturedTotals") or {}
    refunded = amounts.get("refundedTotals") or {}
    total = cents(amounts.get("netTotal", amounts.get("orderAmount", 0)))
    refund = abs(cents(refunded.get("orderAmount")))
    # netTotal includes tax, but not tips. Refund orderAmount also includes tax.
    sales = total - refund
    tips = cents(captured.get("tipAmount")) - abs(cents(refunded.get("tipAmount")))
    return sales, tips


def completed(order):
    statuses = order.get("statuses") or {}
    if statuses.get("status") == "CANCELLED":
        return False
    state = statuses.get("transactionStatusSummary")
    # Preserve completed sales when their summary changes following a refund.
    return state == "COMPLETED" or (state in {"REFUNDED", "PARTIALLY_REFUNDED"}
        and cents(((order.get("amounts") or {}).get("capturedTotals") or {}).get("orderAmount")) > 0)


def categories(order, sales):
    """Allocate tax/discounts/fees/refunds by item value; sum equals sales."""
    groups = defaultdict(lambda: {"quantity": Decimal(0), "weight": Decimal(0)})
    for item in order.get("items") or []:
        if item.get("status") == "RETURNED":
            continue
        sku = fix_sku(str(item.get("sku") or "")).strip()
        category = sku_prefix_to_category_map.get(sku.split("-", 1)[0], "Uncategorized")
        qty = number(item.get("quantity"))
        groups[category]["quantity"] += qty
        groups[category]["weight"] += max(Decimal(0), number(item.get("unitPrice")) * qty - abs(number(item.get("discount"))))
    total_weight = sum((g["weight"] for g in groups.values()), Decimal(0))
    if not total_weight:
        result = {name: {"quantity": float(g["quantity"]), "sales_cents": 0} for name, g in groups.items()}
        result.setdefault("Uncategorized", {"quantity": 0, "sales_cents": 0})["sales_cents"] += sales
        return result
    result = {}
    remainder = sales
    names = sorted(groups)
    for index, name in enumerate(names):
        amount = remainder if index == len(names) - 1 else cents(Decimal(sales) * groups[name]["weight"] / total_weight)
        remainder -= amount
        result[name] = {"quantity": float(groups[name]["quantity"]), "sales_cents": amount}
    return result


def order_pace(orders, start, end, now):
    """Completed-order arrival gaps, attributed to the newer order's unit count.

    Match the report's fastest-10%-median benchmark; recent medians include
    quiet gaps. UTC elapsed seconds avoid local clock/DST ambiguity.
    """
    eligible = []
    for order in orders:
        if not completed(order):
            continue
        try:
            at = instant(order.get("createdAt"))
        except (ValueError, TypeError):
            continue
        if start <= at < end and at <= now:
            eligible.append((at, order))
    eligible.sort(key=lambda pair: pair[0])
    samples = {size: [] for size in (1, 2, 3)}
    recent = {size: [] for size in samples}
    for (previous, _), (at, order) in zip(eligible, eligible[1:]):
        quantity = sum((number(item.get("quantity")) for item in order.get("items") or []), Decimal(0))
        if quantity not in samples:
            continue
        seconds = (at - previous).total_seconds()
        samples[quantity].append(seconds)
        if at >= now - timedelta(minutes=10):
            recent[quantity].append(seconds)
    def median(values):
        values = sorted(values)
        if not values:
            return None
        middle = len(values) // 2
        return (values[middle] + values[~middle]) / 2
    return [{"items": size, "recent_seconds": median(recent[size]),
             "recent_samples": len(recent[size]), "today_samples": len(samples[size]),
             "fast_seconds": median(sorted(samples[size])[:min(10, max(1, (len(samples[size]) + 9) // 10))])}
            for size in samples]


def kitchen_intake(orders, now):
    """Rolling arrival quantities, independent of midnight and order-size groups."""
    recent_start = now - timedelta(minutes=5)
    baseline_start = now - timedelta(minutes=20)
    recent_items = Decimal(0)
    baseline_items = Decimal(0)
    recent_orders = 0
    for order in orders:
        if not completed(order):
            continue
        try:
            at = instant(order.get("createdAt"))
        except (ValueError, TypeError):
            continue
        if not baseline_start <= at <= now:
            continue
        # Arrival workload uses original quantities, including later returns.
        quantity = sum((max(Decimal(0), number(item.get("quantity")))
                        for item in order.get("items") or []), Decimal(0))
        if at >= recent_start:
            recent_items += quantity
            recent_orders += 1
        else:
            baseline_items += quantity
    rate = recent_items / 5
    baseline = baseline_items / 15
    trend = "steady"
    if rate > baseline * Decimal("1.2"):
        trend = "rising"
    elif rate < baseline * Decimal("0.8"):
        trend = "falling"
    return {"items_per_minute": float(rate), "recent_items": float(recent_items),
            "recent_orders": recent_orders, "baseline_items_per_minute": float(baseline),
            "trend": trend}


def store_details(orders, start, end, now):
    """Today's incoming orders, plus sold SKU quantities; no customer/payment fields."""
    rows = []
    skus = defaultdict(Decimal)
    for order in orders:
        try:
            at = instant(order.get("createdAt"))
        except (ValueError, TypeError):
            continue
        if not start <= at < end or at > now:
            continue
        items = [{"name": str(item.get("name") or ""),
                  "sku": fix_sku(str(item.get("sku") or "")),
                  "quantity": float(number(item.get("quantity"))),
                  "status": str(item.get("status") or "")} for item in order.get("items") or []]
        currency = (order.get("amounts") or {}).get("currency") or "USD"
        sales, tips = money(order)
        rows.append({"id": str(order.get("id") or ""), "number": str(order.get("orderNumber") or order.get("id") or ""),
                     "created_at": utc_iso(at), "sales_cents": sales, "tips_cents": tips, "currency": currency,
                     "status": str((order.get("statuses") or {}).get("transactionStatusSummary") or
                                   (order.get("statuses") or {}).get("status") or "OPEN"), "items": items})
        if completed(order) and currency == "USD":
            for item in items:
                if item["status"] != "RETURNED":
                    skus[item["sku"] or "Unknown SKU"] += number(item["quantity"])
    rows.sort(key=lambda row: (row["created_at"], row["id"]), reverse=True)
    return {"kitchen_intake": kitchen_intake(orders, now), "order_pace": order_pace(orders, start, end, now), "latest_orders": rows[:20], "sku_counts": [{"sku": sku, "quantity": float(qty)}
            for sku, qty in sorted(skus.items(), key=lambda pair: (-pair[1], pair[0]))]}


def summarize(store, orders, now, historical=False):
    zone = ZoneInfo(store.timezone_name)
    day = now.astimezone(zone).date()
    start, end = local_day_bounds(day, zone)
    hourly = []
    bucket = start
    # Advance absolute hours: repeated local hours stay separate on fall DST days.
    while bucket < end and bucket <= now:
        local = bucket.astimezone(zone)
        hourly.append({"at": utc_iso(bucket), "label": local.strftime("%I %p").lstrip("0") + " " + local.tzname(),
                       "partial": not historical and bucket <= now < bucket + timedelta(hours=1), "sales_cents": 0, "orders": 0})
        bucket += timedelta(hours=1)
    recent_start = now - timedelta(minutes=5)
    baseline_start = recent_start - timedelta(hours=1)
    eligible = []
    currency_warning = False
    for order in orders:
        if not completed(order):
            continue
        if (order.get("amounts") or {}).get("currency", "USD") != "USD":
            currency_warning = True
            continue
        try:
            at = instant(order.get("createdAt"))
        except (ValueError, TypeError):
            continue
        if at <= now:
            eligible.append((at, order, *money(order)))
    today = [(at, order, sales, tips) for at, order, sales, tips in eligible if start <= at < end]
    totals = defaultdict(lambda: {"quantity": 0, "sales_cents": 0})
    for at, order, sales, tips in today:
        idx = int((at - start).total_seconds() // 3600)
        hourly[idx]["sales_cents"] += sales
        hourly[idx]["orders"] += 1
        for name, row in categories(order, sales).items():
            totals[name]["quantity"] += row["quantity"]
            totals[name]["sales_cents"] += row["sales_cents"]
    recent = [row for row in eligible if recent_start <= row[0] <= now]
    baseline = [row for row in eligible if baseline_start <= row[0] < recent_start]
    # No historical operating-hours assumption: require a sale preceding the
    # complete comparison hour before describing relative activity.
    has_history = any(row[0] <= baseline_start for row in eligible)
    ratio = len(recent) / (len(baseline) / 12) if has_history and baseline else None
    if not recent:
        pace = "Quiet right now"
    elif not has_history:
        pace = "Building baseline"
    elif not baseline:
        pace = "Activity starting"
    elif ratio >= 1.5:
        pace = "Busy"
    elif ratio < .75:
        pace = "Slower"
    else:
        pace = "Steady"
    activity = []
    for index in range(12):
        lower = now - timedelta(minutes=60) + timedelta(minutes=5 * index)
        upper = lower + timedelta(minutes=5)
        activity.append({"at": utc_iso(lower), "label": upper.astimezone(zone).strftime("%I:%M %p").lstrip("0"),
                         "orders": sum(lower <= row[0] < upper or
                             (index == 11 and row[0] == now) for row in eligible)})
    total_sales = sum(row[2] for row in today)
    result = {"id": store.id, "name": store.display_name or store.poynt_name, "store_id": store.store_id,
            "timezone": store.timezone_name, "date": day.isoformat(), "sales_cents": total_sales,
            "historical": historical,
            "tips_cents": sum(row[3] for row in today), "order_count": len(today),
            "item_count": float(sum((number(item.get("quantity")) for _, order, _, _ in today
                                      for item in (order.get("items") or []) if item.get("status") != "RETURNED"), Decimal(0))),
            "average_sale_cents": cents(Decimal(total_sales) / len(today)) if today else 0,
            "recent_orders": len(recent), "recent_sales_cents": sum(row[2] for row in recent),
            "pace": pace, "pace_ratio": round(ratio, 2) if ratio is not None else None,
            "baseline_orders": len(baseline), "last_sale_at": utc_iso(max((row[0] for row in eligible), default=None)),
            "hourly": hourly, "activity": activity,
            "categories": [{"name": name, **row, "share": round(row["sales_cents"] / total_sales * 100, 1) if total_sales else 0}
                           for name, row in sorted(totals.items(), key=lambda pair: (-pair[1]["sales_cents"], pair[0]))],
            "currency_warning": currency_warning}
    if historical:
        result.update(recent_orders=None, recent_sales_cents=None, pace=None,
            pace_ratio=None, baseline_orders=None, last_sale_at=utc_iso(max((row[0] for row in today), default=None)), activity=[])
    return result
