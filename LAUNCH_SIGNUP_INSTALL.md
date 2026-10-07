# Launch signup installation

This patch includes the teaser page plus native signup and optional feedback, explicit consent, Turnstile validation, double opt-in, a private review/CSV page, and Postmark Subscription Change suppression handling. No announcements are sent by this patch. A message builder prepares future Broadcast batch items with Postmark-managed unsubscribe links.

1. Copy these files into the matching folders of your CURRENT app. This package does not replace main.py or other existing routes.
2. From your app root run: python install_launch_signup.py
3. With your intended database environment loaded, run: alembic upgrade head
   The migration follows b81e742d9c30, the head in the supplied source. If your current head has changed, reconcile that chain first; do not apply to the wrong database.
4. Configure the environment settings below, then restart/deploy.

Existing configuration: SESSION_SECRET, DATABASE_URL, APP_BASE_URL, POSTMARK_SERVER_TOKEN, EMAIL_FROM. Confirmation emails use the existing transactional outbound stream.

New settings:
- TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY: Create a Cloudflare Turnstile widget for foodtruckworks.com (and your local test hostname if needed). Server validation checks the hostname against APP_BASE_URL and action launch-signup. Signup fails closed until both keys are set. Never use test keys in production.
- LAUNCH_ADMIN_USER_IDS: comma-separated IDs of specific active user accounts allowed to review the list, e.g. 1,2. These are application USER IDs, not organization IDs. Default grants nobody access.
- POSTMARK_BROADCAST_STREAM: the ID of a Broadcast-type Message Stream created on your existing Postmark server. Do not use outbound. The future message builder requires this value. Verify its type in Postmark before any campaigns.
- LAUNCH_EMAIL_FROM: your verified, monitored announcement sender, e.g. Food Truck Works <updates@your-verified-domain>. Configure a separate authenticated broadcast subdomain if following Postmark's sender reputation recommendation.
- POSTMARK_WEBHOOK_USERNAME and POSTMARK_WEBHOOK_PASSWORD: dedicated strong random HTTP Basic credentials for webhook authentication. Do not reuse your login or API token.

Postmark setup:
- Create a Broadcast stream, then configure a Subscription Change webhook for https://foodtruckworks.com/launch-updates/postmark, supplying the dedicated Basic Auth credentials in Postmark's webhook configuration. Enable the same webhook on outbound to record bounced confirmation addresses too.
- Test webhook authentication and an actual Subscription Change event. The handler records suppression events and never automatically reactivates local subscriptions.
- Broadcast announcements must use /email/batch with MessageStream explicitly specified on every item. Use Postmark-managed unsubscribe links supplied by build_broadcast_message; configure the stream's unsubscribe handling accordingly. Campaign sending UI/queue is not part of this patch.
- Review bounce/complaint statistics before and after campaigns. Postmark's stated limits are bounce rate below 10%, spam complaint rate below 0.1%. Automatic campaign pausing/alerts are not implemented by this signup patch.
- Keep permission fresh. Do not collect addresses for many months without communicating and then launch to a stale list. Build campaign eligibility and consent freshness checks when adding campaign sending.

Review signups at /admin/launch-updates while logged in as an allowlisted user. Export includes pending/suppressed records with explicit status; do not use the whole CSV as a send list. Only confirmed, unsuppressed, sufficiently recent opt-ins are eligible. Feedback is escaped in the admin view, and CSV values are protected against spreadsheet formula injection.

Public form is available at / and /launch-updates. Logged-in users keep their normal /dashboard redirect from /. Confirmation links open a page with a POST confirmation button so email scanners do not subscribe people by fetching links. Links expire after 24 hours, and repeat confirmation requests have a ten-minute cooldown. Pending tokens are stored as hashes. Existing confirmed or suppressed signups are not overwritten or silently reactivated.

The shared-database throttle permits ten signup attempts per source connection IP per ten minutes. If your hosting proxy presents a shared IP, configure trusted proxy handling correctly and add a Cloudflare rate rule for POST /launch-updates. The implementation deliberately does not trust arbitrary forwarded headers.

Remove LAUNCH_UPDATES_URL if previously set; the native form replaces the external signup link. Bypass CDN caching for /, /launch-updates, /launch-updates/*, and /admin/launch-updates*. Responses carry no-store headers; ensure any Cloudflare cache-everything rule does not override them.

The automated tests mock external email and CAPTCHA calls. Live Postmark delivery, production PostgreSQL migration, webhook setup, and actual Turnstile credentials require deployment verification. Existing auth/register gates are unchanged. No marketing mail was sent while preparing this patch.
