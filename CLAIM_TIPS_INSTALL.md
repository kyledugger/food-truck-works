# Claim Tips workflow

Extract this ZIP into the application root, preserving folders and replacing the included files. Restart the local app, test, then commit/push using your normal deployment process. No migration, schema change, new dependency, or credential change is required.

This patch is based on the source_package(2).zip uploaded October 5 during the tip-submission debugging conversation. If these files have since changed, merge the changes instead of overwriting newer work. The package contains only changed/new files, not the whole application.

## Behavior

- Dashboard and store dashboard now launch Claim Tips. Orders Report retains total tips and tip percentage, but has no tip calculator, Submit Tips, Tip History, or Tip Settings buttons.
- Owners/managers/payroll can choose active stores in their organization. Regular members require an active StoreAssignment. Configure missing staff assignments through the existing store settings workflow before testing. No assignment is created automatically.
- Store displays use their assigned store without a picker. The middleware and both load/submission routes enforce current assignment and session version. They return to the store dashboard after a successful claim.
- The server captures launch time and floors it to a whole minute. Choosing another store/date or changing the start preserves this end boundary. Launch again from the dashboard to capture a newer end time.
- A recent accepted claim end is suggested. Employees/displays may choose Today or Yesterday, with a maximum 24 elapsed hours. Managers/payroll may choose older start dates. Today/Yesterday windows end at launch; older historical dates end at their next local midnight. On a 25-hour daylight-saving day the window is limited to the final 24 elapsed hours.
- Starts cannot precede the relevant latest accepted claim end, activation time, or 24-hour lower limit. Later starts and gaps between claims are allowed. Rejected submissions do not establish a boundary. Legacy future-ending reports do not become start suggestions; their claimed orders are still excluded.
- Old second-resolution claim boundaries are rounded up for the earliest whole-minute start. Existing records are preserved. A partial legacy minute can therefore require a separate manager correction; new claims have exact whole-minute boundaries.
- The existing configured employee submission deadline still applies, and manager/payroll exemptions remain. The 24-hour window also limits how much one claim can cover.
- Every new range is [start, end). An order exactly at the end minute remains for the next range/claim. Actual order seconds are never rounded. DST gaps/repeated wall times are rejected rather than guessed.
- The calculator retains employee selectors, up to six ranges, employee totals, cash/paycheck policy and odd-cent allocation. Range split points now use whole minutes.
- Already claimed order IDs are excluded on load and checked again on submission. Total, already claimed and available tips are shown. Concurrent changes require reload; database uniqueness remains the final duplicate-payment safeguard. Submission and payouts commit together.
- Launch/loaded boundaries are bound to the signed user session. New launches invalidate an older calculator in another tab. Submission rechecks the store, active employees, business, dates, activation/deadline and minute boundaries.

## Verification performed

25 focused Python tests passed: claim windows, overnight/DST behavior, historical dates, store authority/display session validation, half-open allocation, existing claims, concurrent allocation, commit/replay, display return, existing timezone tests and tip report totals. Python compilation, route imports, Jinja template rendering and JavaScript syntax checks passed. DOM interaction checks covered employee allocation/odd cents, range splitting, whole-minute inputs, a viewer timezone different from the store, read-only display store and start validation.

Live Poynt calls and production deployment were not exercised. A full browser visual run was unavailable in this environment. No production database was accessed or changed.

## Local acceptance checks

1. Open Orders Report. Confirm sales/report functionality and total tips/tip percentage remain.
2. Launch Claim Tips as a manager, choose a store, confirm the suggested start and fixed end, load tips and divide them among employees.
3. Try an employee with a valid store assignment. Earlier starts, windows over 24 hours, future dates, unassigned stores and dates older than yesterday must be rejected.
4. Test an overnight shift with Yesterday at 9 PM and launch at 1:30 AM. Compare available tips against Poynt and existing history.
5. Test a store display. The store is read-only, another store cannot be requested, and successful submission returns to its dashboard.
6. Test two calculators claiming overlapping tips. The second must require a reload, without duplicate payouts.

Tests (from application root):

```powershell
python -m unittest discover -s tests -p "test_claim*.py" -v
python -m unittest discover -s tests -p "test_store_time.py" -v
python -m unittest discover -s tests -p "test_tip_report_totals.py" -v
```

The isolated claim tests create only an in-memory SQLite database and stub Poynt responses. They do not use the app's configured database for their test operations.
