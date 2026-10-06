# Store display home and performance layout

Apply after the Claim Tips and Orders Report access patches. Extract into the application root, preserving paths, and restart the app. No migration or new runtime dependency is required. The included files contain the earlier Claim Tips middleware and store-dashboard button changes.

## New flow

- Store display login and /dashboard open the assigned store's home screen.
- Home contains Claim Tips and Store Performance, without sales figures, charts, orders or sales-data polling.
- Claim Tips launches directly for that store. Its existing dashboard/back and successful-submit paths now return to this home screen.
- Store Performance is opened explicitly. It continues updating until hidden; there is no auto-hide timer.
- Hide Performance immediately blanks the current page and opens home. Home clears the display session's performance flag. Direct dashboard URLs redirect home and sales-data requests are denied while performance is closed.
- Returning to a performance page from browser back/forward cache redirects home rather than restoring its figures. Display pages remain no-store.
- Current assignment and session-version checks still apply. Another store cannot be opened. Opening performance requires the home form's session token. This is a visibility control, not a manager PIN restriction.
- Owners/managers retain their normal dashboard access. They can also use Hide Performance and the same store home screen.

## Layout

The left column stacks the sales card, Kitchen Intake, then Time Between Orders. Latest Orders occupies the entire right column. On narrower screens the columns stack. At desktop heights, each column can scroll independently. Sales KPIs use two columns to fit the narrower sales card.

## Validation

All 49 existing/updated live-dashboard tests passed, including login, assigned-store access, session revocation, closed/open performance access, bad-token rejection and hiding/reopening performance. Python compilation, template rendering and privacy-script syntax checks passed. Navigation-script checks confirm immediate blanking and back/forward-cache redirection.

No production database or live Poynt service was used. Full visual browser testing was unavailable; verify the layout on your actual tablet before deploying.

## Local checks

1. Sign in as a store display. Confirm only the assigned store's home and the two actions appear, without sales data.
2. Open Claim Tips directly. Exit/back or submit and return to the home screen.
3. Open Store Performance. Confirm live updates and the new two-column layout.
4. Click Hide Performance. Confirm home appears immediately. Browser Back should not restore visible sales figures.
5. Reopen performance deliberately, and verify it remains up for kitchen use.
6. Check both landscape and portrait tablet views and owner/manager dashboards.

```powershell
python -m unittest discover -s tests -p "test_live_dashboard.py" -v
```

The test suite uses isolated SQLite databases, not your configured production database.
