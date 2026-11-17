# Automatic GoDaddy kitchen notification setup

This patch overlays source_package(7).zip. Extract its files into the FTW project root, preserving subdirectories. It is a change package, not a complete copy of the application.

## Behavior

- Integrations now has a GoDaddy kitchen notifications card with per-store checkboxes, setup status, last verification, and last received notification. Times on this page are explicitly UTC.
- Save stores and verify setup registers the transaction webhook automatically for the connected business. Repeated setup lists registrations before creating anything. Stores share that business registration.
- GoDaddy OAuth completion also prepares the registration and opens Integrations to select stores. Existing store discovery/configuration remains available through the linked store settings page; stores need their normal FTW setup before kitchen service can run.
- One durable registration lease serializes OAuth, manual setup and background checks. Secrets are generated once and encrypted with the application's existing integration credential encryption.
- Successful registration is checked again after an hour; failed registration retries after five minutes when that organization's dashboard worker next runs. A missing registration is recreated. Duplicate, inactive, or changed registrations report a setup error rather than modifying an unexpected hook. Manual verification is available immediately.
- A POST timeout preserves the generated secret and callback identity. Retry first lists hooks to recover an already-created registration rather than blindly creating another.
- Notifications still only trigger lookups for selected active stores with active kitchen service. Existing transaction lookup budget, retry rules, order webhooks, manual Refresh and 30-second active-kitchen fallback remain.
- No setup route deletes or rewrites order webhooks. Registrations for previous connections remain stored but notifications must match the currently connected business. Unchecking every store stops kitchen lookups; the hook may remain registered.

## Deployment operator configuration

These are platform configuration, not subscriber setup:

1. `INTEGRATION_TOKEN_KEYS` must contain the existing Fernet integration encryption key(s). This is already used by Square integration storage. If configured, retain the existing value. Do not regenerate it on redeploy; losing it prevents decryption. New deployments can generate a key locally with:

   ```powershell
   .\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Store that output privately in server configuration. Never paste it into chat or commit it.

2. Callback URLs use your existing `APP_BASE_URL` HTTPS origin, falling back to the origin of `POYNT_REDIRECT_URI` when unset. No additional public URL environment variable is needed. They never use an incoming Host header. The origin must be reachable by Poynt. Already-saved registration URLs are retained so this change does not silently replace a working webhook.
3. `POYNT_APP_ID` and the normal GoDaddy OAuth application configuration remain required.
4. `POYNT_TRANSACTION_TRIGGER_ENABLED` defaults to `1`. Set it to `0` as a platform emergency switch; order polling remains available. Disabled managed callbacks return 503 so the provider can retry.

## Database migration

The included migration adds only `poynt_transaction_webhooks` and `poynt_transaction_store_settings`. No store preferences or provider registrations are changed by the schema migration. Its parent is the pilot revision `f74c28a610be`; its new revision is `b13d82e69a04`.

Run the read-only preflight against your explicitly selected database:

```powershell
.\.venv\Scripts\python.exe .\apply_transaction_webhook_settings_migration.py --dotenv .env.local-prod-db
```

If it reports parent `f74c28a610be` and passes preflight, the following command explicitly applies the two new tables to that same database:

```powershell
.\.venv\Scripts\python.exe .\apply_transaction_webhook_settings_migration.py --dotenv .env.local-prod-db --apply
```

If the revision differs, stop and review the output. Do not stamp the database. Use the new helper, not the earlier pilot migration helper. Apply the migration before starting the new application code; the Integrations page requires the new tables. This package has not been applied to any production database.

## Transition your current pilot

1. Keep the existing pilot environment variables in place for the first deployment. Ensure the encryption configuration above is ready.
2. Apply the migration, deploy the changed application files, and open Integrations as an owner or manager.
3. Check the desired store(s), then click **Save stores and verify setup**. Setup imports the existing pilot hook ID and signing secret into encrypted database storage, preserving its callback URL. It verifies that registration against the connected business. It does not intentionally create a second pilot hook.
4. Confirm setup status **ready**, run a credit and a cash test with kitchen service active, and confirm the last notification timestamp advances.
5. After confirming the database-backed route works, remove `POYNT_TRANSACTION_TRIGGER_STORE_IDS`, `POYNT_TRANSACTION_PROBE_ORGANIZATION_ID`, `POYNT_TRANSACTION_PROBE_SECRET`, `POYNT_TRANSACTION_PROBE_HOOK_ID`, `POYNT_TRANSACTION_PROBE_ENABLED`, and `POYNT_TRANSACTION_LOOKUP_ENABLED` from deployment configuration. The adopted callback authenticates using the database even though its URL retains the old `transaction-probe` name. The global emergency switch and optional request-budget setting can remain.

For new subscribers, no pilot variables or registration script are required. They connect GoDaddy, configure their stores through FTW if needed, and choose stores on Integrations.

## Validation and limits

Validated with local SQLite and mocked provider requests: multi-store selection, tenant isolation, permissions/CSRF, encrypted secret retention, signed notification delivery and deduplication, pilot adoption without environment credentials, repeated registration, POST-timeout recovery, registration lease exclusion, duplicate-registration refusal, provider request shapes, and actual Alembic preflight/upgrade. Existing transaction-trigger tests and 226 kitchen regression tests passed.

No live GoDaddy registration or production migration was performed. PostgreSQL concurrency and provider permissions still need deployment verification. Retain the 30-second fallback during that verification.

Provider reference: https://docs.poynt.com/app-integration/cloudApps/webhooks.html
