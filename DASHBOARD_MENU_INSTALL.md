# Dashboard navigation and timestamps

Extract into the project root, deploy and reload the dashboard. No migration or configuration change is required.

The dashboard header and footer navigation buttons are replaced by a hamburger menu, preserving existing role and connection visibility. Settings and Experimental expand inside the menu. Business switching remains beside the menu. Logout remains a POST. Escape or clicking outside closes the menu.

Additional-payment check timestamps now share the top sales-status line. The oldest available completed check represents freshness across stores; the line indicates pending checks when needed. Hover for individual store check times. The line stays single-line and scrolls horizontally on narrow screens. Payment errors/review warnings remain on their cards, but ordinary checked timestamps are removed. All store-card status badges are removed, including historical/loading badges.

Only dashboard.html, live-dashboard.css, live-dashboard.js and the new dashboard-menu.js are included. Prior kitchen changes are preserved. Template/role rendering, JavaScript syntax and DOM interaction checks passed. Visual browser screenshots were not available.
