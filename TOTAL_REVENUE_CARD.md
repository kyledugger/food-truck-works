# Daily total revenue card

Copy templates/dashboard.html and static/js/live-dashboard.js into the FTW
project, keeping their paths, then restart/redeploy.

Adds Total revenue for the day above the individual store cards. It sums
the same integer-cent sales figures already used by those cards, includes
tax, excludes tips, and reflects refunds through the existing calculation.
It follows Today or the selected historical date and refreshes with the
dashboard. Loading/setup gaps are labeled as partial totals; disconnected
or empty dashboards hide the card. Delayed updates are labeled.

The script URL version is updated so browsers load the new script.
No server logic or database migration changes are required. This UI patch
is separate from the earlier offline-sales correction; keep that fix installed.
