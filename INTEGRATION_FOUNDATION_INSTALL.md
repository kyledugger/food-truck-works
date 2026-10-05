# Integration foundation

This patch adds an owner/manager Integrations page with Square account authorization,
encrypted credentials, on-use refresh, verified directory access, and explicit mappings
to existing FTW stores/employees. It does not import sales, timecards, or schedules yet.
Poynt tables, credentials, reports, and store IDs are unchanged.

## Install

1. Back up your source and database. The ZIP contains the complete updated source
   package. If you have made changes since the uploaded snapshot, copy only these
   changed/new files into your project: `integrations/`, `routers/integrations.py`,
   `templates/integrations.html`, `tests/test_integrations.py`, this guide, and
   `alembic/versions/a61f0d8c3b92_integration_foundation.py`. Also merge the small
   additions in `main.py`, `models.py`, and `templates/dashboard.html`.
2. Install your normal requirements. This uses existing httpx and cryptography
   dependencies; no Square SDK is required.
3. Generate an encryption key in your own environment:
   `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
   Set `INTEGRATION_TOKEN_KEYS` to that value in local configuration and Render.
   Keep it private and backed up. Losing it makes stored integration tokens unreadable.
   Rotation: use `new-key,old-key` until old tokens have been re-encrypted through refresh
   or reconnection. Do not remove old keys while any ciphertext still needs them.
4. Create a Square application at https://developer.squareup.com/apps.
   For each environment you intend to use, set:

   ```text
   SQUARE_SANDBOX_APPLICATION_ID=...
   SQUARE_SANDBOX_APPLICATION_SECRET=...
   SQUARE_SANDBOX_REDIRECT_URI=https://your-test-host/integrations/square/callback
   SQUARE_PRODUCTION_APPLICATION_ID=...
   SQUARE_PRODUCTION_APPLICATION_SECRET=...
   SQUARE_PRODUCTION_REDIRECT_URI=https://foodtruckworks.com/integrations/square/callback
   SQUARE_API_VERSION=2026-09-16
   ```

   Register each exact redirect URI on the Square application's OAuth page for that
   environment. Use HTTPS for testing too (an HTTPS test deployment or local tunnel).
   Configure only the environments you need. Test with a Sandbox seller first.
   The initial OAuth scopes are MERCHANT_PROFILE_READ and EMPLOYEES_READ only.
5. **Verify your migration target before running any migration.** The repository's
   Alembic env loads `.env` directly, while the application follows DOTENV_FILE.
   Set DATABASE_URL explicitly to the intended local development database and check
   `python -m alembic current`. The expected prior head is `84ac2e7b91d0`.
   Then run `python -m alembic upgrade head` against that confirmed local database.
   This adds three tables; it does not migrate Poynt credentials or update existing rows.
   Production migration is a separate deployment step after local validation and backup.
6. Restart the application. Dashboard > Integrations > Sandbox > Connect Square.
   Link locations and team members manually. Check that Poynt reports still work.

## Validation

Run `python -m unittest discover -s tests -p test_integrations.py`.
Tests use an isolated SQLite in-memory database and mocked HTTP. No real Square
credentials or business database are required. Also validate a real sandbox OAuth
round trip, cancellation, reconnection, revocation, and mapping in your environment.

## Boundaries and next steps

- Connections belong to organizations, not the individual who authorized them.
- Multiple Square accounts and both environments are supported. A given Square
  merchant/environment is restricted to one FTW organization to prevent ambiguous
  ownership and cross-business revocation.
- Existing stores still contain legacy Poynt fields. This patch links Square to them
  without manufacturing Poynt IDs or automatically creating stores/resources. A
  Square-only organization needs provider-neutral store creation in a subsequent step.
- One external location maps to one FTW store per connection; one team member maps
  to one FTW employee. Mapping writes validate both external identity and tenant ownership.
- `ProviderDefinition` separates provider capability support from granted permissions.
  Directory access uses a small protocol and provider-specific translation. Other
  capabilities are declared for later implementation, not exposed as working features.
- OAuth state is server-side, hashed, short-lived, single-use, and bound to user/org.
  Signed browser sessions contain state/CSRF nonces only, never access/refresh tokens.
- Refresh is serialized with a PostgreSQL row lock and occurs on use after seven days
  or near expiry. Background proactive renewal is not installed yet; add it before
  unattended synchronization. API failures show generic messages without credentials.
- Disconnect revokes authorization in Square before clearing local tokens. Revocation
  failure retains local credentials for retry. Seller-side revocation is detected on
  the next verification; webhook revocation handling comes with the sync service.
- A status verification failure is shown explicitly. Last verification is historical;
  connection state cannot establish that a person has been paid through payroll.
- No payroll export capability is advertised until its data path is verified.
- Future work: provider-neutral stores, feature source selection, sales/timecard models,
  worker-based synchronization, webhooks/reconciliation, schedules, payroll preparation.
- Periodically remove used/expired integration_oauth_attempts through a maintenance job.

Official reference: https://developer.squareup.com/docs/oauth-api/overview
and https://developer.squareup.com/docs/oauth-api/receive-and-manage-tokens
