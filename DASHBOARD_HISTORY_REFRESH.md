# Historical dashboard refresh policy

Apply this patch over the preceding dashboard cache patch. No migration or environment changes are required.

- The background worker queues the previous day's full order scan on its next ordinary sync after that day has ended in all configured store timezones. It does not need an open dashboard. A successful full-day load prevents repeated closing scans.
- Yesterday's verified historical data is eligible for another provider check after one hour; older dates after 24 hours. These further checks run when the date is viewed. A historical dashboard left open schedules a wake-up at the next check time instead of continuously polling fresh data.
- The historical dashboard's Refresh button explicitly requests a provider check, bypassing the normal schedule. It waits for the result when the organization sync lease is available. If another sync is running, the request is queued and the existing worker processes it. Cached totals stay visible.
- Failed provider checks retain the saved order data and schedule a retry after one minute while the historical request remains active. Revisiting the date or using Refresh allows another attempt.
- Today and the kitchen queue keep their existing refresh behavior. This patch does not introduce stored aggregate snapshots or change sales calculations.

Existing historical records may receive one more check at their previously saved five-minute deadline; after success, the new hourly/daily interval applies.

Validation: dashboard and kitchen SQLite test suites, including timezone/DST scheduling, first full-day queuing, idempotency, manual refresh, origin protections, sync contention, and provider failures. DOM checks cover cached/empty/loading/error states, manual refresh, and the next-check timer. No production database or provider writes were performed during development.
