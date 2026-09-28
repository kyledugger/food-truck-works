# Time and timezone conventions

**Status:** Engineering standard for Food Truck Works  
**Applies to:** Database models, Poynt integrations, time cards, scheduling, tips, reports, APIs, and user interfaces.

## The rule

Store an **instant** as an aware UTC datetime in Python and PostgreSQL `timestamp with time zone` (`timestamptz`). Store a **local calendar intention** as local date/time plus an IANA timezone, such as `America/Phoenix`. Convert only at a named boundary. Never infer a store, event, or employee's timezone from the web browser, server clock, or database session.

UTC answers *when did it happen?* A local date and timezone answer *which business day, or what clock time was promised at this place?* These are different questions. PostgreSQL `timestamptz` retains an instant, not the original timezone name. Keep the timezone separately wherever its identity matters.

| Meaning | Persist | Examples |
| --- | --- | --- |
| Observed action or deadline | `timestamptz` UTC instant | `clocked_in_at`, `submitted_at`, `paid_at`, `expires_at` |
| Calendar day without a particular instant | PostgreSQL `date` | A holiday date or event date shown on a calendar |
| One-time future appointment | Local date, local time, and IANA timezone; optionally a derived UTC instant | Event starts at 6 PM at its venue |
| Recurring wall-clock rule | Weekday or recurrence rule, local time, and IANA timezone | Open every Monday at 9 AM |
| Elapsed duration | Integer seconds or another explicit unit | Actual shift length, break length, 24-hour tip window |

Use `_at` for instant columns and `_date`, `_time`, and `_timezone` or `timezone_name` for civil values. Do not put offset-free local times in an `_at` field. A date or time without a zone must never be silently treated as UTC.

## Ownership of the timezone

- A Poynt store has a configured IANA timezone for its normal operating location and store-local reports.
- An event has the **venue's timezone**, even when the truck's usual store timezone differs. Save that timezone with the event.
- A scheduled shift uses the location where the employee is scheduled to work. Save the timezone with the shift, rather than depending solely on a mutable store setting.
- A recorded time-card punch is an instant. Retain the shift or location timezone separately for the employee-facing clock and payroll day.
- If a Poynt store ID moves across timezones, a single mutable timezone field is insufficient for historical reporting. Introduce effective-dated store timezone assignments or snapshot the zone on the relevant records before supporting that workflow.
- An employee's browser timezone is a viewing preference only. It must not determine payroll days, order report boundaries, or store business dates.

Use IANA names (`America/Denver`), not fixed offsets (`-07:00`) or abbreviations (`MST`), for future schedules. An offset is valid for a particular instant but does not describe future daylight-saving rules.

## Python and API boundaries

1. Produce instants with `datetime.now(timezone.utc)`. Parse external timestamps only when they contain `Z` or an explicit offset; normalize to UTC immediately. Reject naive instants at API and persistence boundaries. `DateTime(timezone=True)` is a schema mapping, not by itself a validator of Python input.
2. Poynt order timestamps and token expirations are instants. Parse their offsets and keep them aware. Do not apply the store timezone until calculating a store-local boundary or formatting a result.
3. Serialize instants as ISO 8601 with `Z` or an explicit offset, for example `2026-09-28T16:30:00Z`. Never send an offset-free datetime to JavaScript and expect the browser to guess its meaning.
4. A `datetime-local` HTML field has **no timezone**. Pair its value with the explicitly selected event/store timezone. Validate the pair on the server before converting to an instant. Do not use `new Date(input.value)` as the conversion for store-local time.
5. Use `ZoneInfo` and `astimezone()` to display an instant. Do not attach a timezone to an already known instant with `replace(tzinfo=...)`. The only place to interpret a naive datetime as local is a validated local-input conversion function.
6. Reject nonexistent and ambiguous local times at daylight-saving transitions. Let the user choose a valid time or, for a repeated hour, an explicit offset/occurrence. Do not silently choose Python's `fold=0` or the browser's default.

Example: a recorded punch is `2026-09-28T16:00:00Z`. Its display in `America/Phoenix` is 9:00 AM. The stored punch remains the same instant if the user travels.

## Database and migration rules

- New instant columns use PostgreSQL `timestamptz` and SQLAlchemy `DateTime(timezone=True)`. Write aware UTC values. Normalize values returned by the driver to UTC in application logic when doing arithmetic or serialization; a database connection may render the same instant in a different session timezone.
- Set application database sessions to UTC for predictable SQL and operational inspection. This is a secondary guard, not permission to send naive timestamps: PostgreSQL can interpret an offset-free input using the session timezone.
- SQL `now()` is an instant and is appropriate for a `timestamptz` server default when a database-side default is intended. Keep ORM and database defaults consistent.
- Calendar-only fields use `date`; recurring hours use local `time` plus a timezone and recurrence definition. Do not convert these to UTC at write time without retaining the civil intention.
- Migrations of legacy `timestamp without time zone` data must state the assumed timezone explicitly (for known UTC values, `AT TIME ZONE 'UTC'`). Audit existing values first. Never rely on the migration connection's session timezone for this cast.
- Alembic owns schema creation and changes. Do not call `Base.metadata.create_all()` at application startup.

## Time cards and shifts

- Store each actual clock-in, clock-out, and break boundary as a UTC instant. Record who made the punch and its source or correction history separately. Never change an old punch merely because a store's timezone setting changes.
- A scheduled shift keeps its local start/end date and time, location timezone, and any derived UTC instants. A shift can cross midnight or a daylight-saving change. Its elapsed duration is calculated from UTC instants, not by subtracting the displayed wall times.
- Payroll grouping, overtime rules, break policy, rounding, and week start are **separate policy decisions**. Define the applicable location/timezone and policy period explicitly before aggregating punches. Do not equate elapsed seconds with payable hours without applying the documented policy.
- For a correction, preserve an audit trail showing the original punch, corrected instant, actor, and reason. Present both values in the shift's timezone while persisting the instants.

## Reports and deadlines

- Interpret a selected local date or date range in the named report timezone. Convert local boundaries to UTC and query with a half-open interval: `start_at <= instant < end_at`. For a whole local day, `end_at` is **the next local midnight**, not `23:59:59` or `start_at + 24 hours`. A daylight-saving day may contain 23 or 25 elapsed hours.
- A single-store report uses that store's timezone. A mixed-timezone report must declare its meaning: either one explicit UTC interval across stores, or each store's local day aggregated after per-store filtering. Never apply the first store's timezone to all stores.
- Use UTC instants for elapsed deadlines. A 24-hour tip submission window means `range_start_at + timedelta(hours=24)`, even across daylight-saving changes. Display the deadline in the relevant store timezone.
- Label charts and exported reports with their timezone. An API response should carry absolute timestamps and the report timezone separately.
- Existing endpoints may have inclusive end-time behavior. Migrate them deliberately, with compatibility checks, when adopting this half-open convention; do not change report totals accidentally.

## Tests required for time-sensitive work

Test each new conversion or report rule with Phoenix (no seasonal clock change), a zone with daylight-saving changes, an overnight shift, a local midnight boundary, a 23-hour spring day, a 25-hour fall day, nonexistent and repeated local times, and a viewer whose browser timezone differs from the store. Test the same UTC instant displayed in two zones, and a multi-store report spanning different zones. Verify both the database query bounds and the user-visible label. For time cards, also test shifts crossing midnight and daylight-saving changes.

## Review questions for a new field or feature

1. Is this an instant, a calendar date, a local wall time, a recurrence, or an elapsed duration?
2. Which place or business rule owns the timezone? Can that location change later?
3. Is the timezone saved with the record, inherited from an immutable context, or effective-dated?
4. What happens at midnight and at daylight-saving gaps and repeated hours?
5. Which interval convention does the report use? What does “today” mean for multiple stores?
6. Does every external/API timestamp have an explicit offset, and does every UI label identify its timezone?

If the answers are unclear, settle the semantics before adding a datetime column.

## References

- PostgreSQL date/time types: https://www.postgresql.org/docs/current/datatype-datetime.html
- PostgreSQL `AT TIME ZONE`: https://www.postgresql.org/docs/current/functions-datetime.html#FUNCTIONS-DATETIME-ZONECONVERT
- SQLAlchemy datetime types: https://docs.sqlalchemy.org/en/20/core/type_basics.html
