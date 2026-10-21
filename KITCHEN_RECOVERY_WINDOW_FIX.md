# Recent kitchen order recovery

Apply these changed files over the previous FTW patches. No migration is required.

Get recent past orders now requests only the selected 10/30/60-minute interval from Poynt instead of first reconciling the full day. Poynt's business orders response is strictly filtered to the current store and the requested timestamps before saving. The normal Refresh button and live reconciliation policy stay unchanged.

The window ends when the search begins, rather than when service started. Search and import share the same server-generated timestamp, so the interval cannot slide while staff review the checkboxes. Searches expire after 15 minutes. Existing tickets, including completed or archived tickets, remain excluded.

The dialog shows the searched time range and each order's local creation time. It displays at most 100 eligible orders, explicitly says when results are truncated, and prevents a previous response from replacing a newer selection.

A narrow search does not mark the full day's sales as reconciled or postpone the normal background scan. It shares the existing organization sync lease, so an already-running sync or a slow Poynt response can still cause a wait. This is not a guaranteed response-time improvement under every network/provider condition.

The source available for this investigation previously capped candidates at 100, so a reported display of 125 candidates was not reproduced. This patch addresses the confirmed full-day fetch and service-start anchoring issues and adds explicit store/window checks. If the deployed app still shows an unexpected pool, compare the displayed search range and order timestamps and provide the currently deployed source for investigation.

Validation uses isolated SQLite and mocked provider responses, including old/other-store/future orders, rolling windows, exact import timestamps, and existing kitchen/dashboard regression tests. No production writes were performed.
