# Organization-specific Poynt order hooks

New registrations use `POYNT_WEBHOOK_URL` plus `/ORGANIZATION_ID`. Keep the environment setting at the base HTTPS URL, for example `https://foodtruckworks.com/webhooks/poynt/orders`; FTW adds the organization ID. Each organization must connect Poynt and explicitly enable live updates through its dashboard. Enabling one organization does not register another.

Callback acceptance requires a valid signature, matching application and order event, and a saved webhook ID tied to the receiving organization's current Poynt business connection. The URL is routing information, not authorization. Only that organization's notification queue receives the event. Multiple organizations may independently connect the same business; duplicate notifications remain deduplicated within each organization.

Existing subscriptions using the base URL remain supported. FTW resolves those by business ID plus their saved hook ID, requiring exactly one matching organization. No automatic re-registration is performed; existing saved subscriptions continue to work without modifying Poynt. A hook not registered in FTW is rejected, as is a disconnected or changed business connection. A shared saved hook ID that matches multiple organizations is rejected rather than arbitrarily assigned.

Deployment: copy the patched files and deploy/restart FTW. No schema migration or environment change is required for this feature. No production database or provider mutation was performed while preparing the patch. Your existing hook should stop receiving the multiple-connection 409 and receive 200 if its ID matches the active business in `dashboard_sync`.

Verify your current binding with a read-only query:

```sql
SELECT organization_id, business_id, hook_id, last_webhook_at
FROM dashboard_sync
WHERE business_id = 'bddd89b0-c3cc-4135-befa-476c89fdf4f1';
```

The existing hook is `a5ea8625-8be1-4107-8b15-eb4d066a9715`. It must be saved on the organization that originally enabled it. If the ID is absent or ambiguous, inspect the registration instead of assigning it blindly. Other organizations with no saved hook can click Enable live updates to register their own organization URL. After a paid test order, check callback HTTP 200, last_webhook_at and notification received/processed timestamps, then verify the kitchen display. Keep the five-minute reconciliation as fallback.

Remove the temporary diagnostic hook using its journal if you have not already done so:

```powershell
python tools/test_poynt_hooks.py --dotenv .env.local-prod-db --organization-id 1 --action delete --confirm-delete
```

That cleanup does not remove the existing FTW registration. This patch does not change eligibility for 100%-discount orders; that remains a separate investigation.
