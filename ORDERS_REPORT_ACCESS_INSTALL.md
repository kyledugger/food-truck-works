# Orders Report access

Apply this patch after the Claim Tips patch. Extract into the project root, preserve folder paths, replace the two application files, and restart the app. No migration or new dependency is required.

Owners and managers keep the Orders Report dashboard button and direct URL access. Employee/member and payroll accounts have no button and receive HTTP 403 if they request /poynt/orders directly. The backend checks access before querying report data or calling Poynt. Claim Tips and Tip History keep their existing permissions.

This patch includes the Claim Tips changes already made in routers/poynt.py and templates/dashboard.html. Merge rather than overwrite if you have newer edits in those files.

Verification: the handler guard passed tests for owner, manager, employee/member, payroll, store display, missing/unknown roles, unauthenticated sessions and missing organizations. Python compilation passed. The dashboard button uses the existing owner/manager permission flag.

Test locally with owner/manager, employee and payroll logins. Check the dashboard button and direct /poynt/orders URL. Run the focused tests with:

```powershell
python -m unittest discover -s tests -p "test_orders_report_access.py" -v
```
