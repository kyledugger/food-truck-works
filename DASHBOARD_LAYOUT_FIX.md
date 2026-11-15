# Dashboard card layout

Copy these files over the corresponding FTW project files, preserving paths:
- templates/dashboard.html
- static/css/live-dashboard.css
- static/js/live-dashboard.js

Restart/redeploy. The main dashboard asset URLs have new version strings to
avoid stale browser caches. This package includes the daily revenue card.
Keep the previously installed offline-sales backend fix.

Store metric layouts now respond to each card's content width, rather than
only the browser width. Cards at or below 560px content width use a two-by-two
metric grid; wider cards keep four columns. Long amounts can wrap within a
metric cell instead of overlapping adjacent numbers. Store headings can wrap
their badges. These layout changes are scoped to the main dashboard, leaving
the dedicated staff store screen layout intact.

Cards with orders, nonzero revenue, or tips display first. Cards still loading
or needing setup come next. Confirmed no-sales cards display last, keeping
their kitchen links accessible. Order updates on the existing refresh cycle;
the revenue total still sums every available store.

No database, API, or revenue calculation changes. No migration required.

Validation: JavaScript syntax and Jinja template syntax checked. Local browser
rendering was blocked by this environment's browser process restrictions, so
the final appearance needs a check after deployment at your usual browser zoom.
