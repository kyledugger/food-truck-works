# Food Truck Works teaser Analytics

Copy install_analytics.py and static/js/ftw-analytics.js into your app root, preserving folders. Run:

    python install_analytics.py

Commit routers/launch_updates.py, templates/coming_soon.html and static/js/ftw-analytics.js, then deploy. No migration or new environment variables. The installer preserves your photos, copy and other template changes, backs up edited files and removes the previously supplied conditional GA snippet. If it reports an unfamiliar source shape, provide the current router/template rather than replacing them with old copies.

## Google Analytics settings

Admin → Data streams → your web stream → Enhanced measurement gear: disable automatic Page changes based on browser history, Form interactions, Site search, Outbound clicks and File downloads. These can automatically send URLs or create form events that do not mean signup success. Manual page_view remains enabled through our script. Scroll tracking may remain enabled. Do not install a second tag in base.html or through Cloudflare.

The two custom events are launch_signup_requested (confirmation email send accepted without an exception) and launch_signup_confirmed (successful database confirmation). Mark launch_signup_confirmed as a key event in Admin → Events. You may create the event entry in advance if it hasn't appeared yet. Use the database for authoritative subscriber totals: browsers can block Analytics, and refresh/network interruptions can lose events. Duplicate/cooldown submissions, errors, expired links and reloads of the thank-you URL do not emit a new conversion. Accepted email sends are not proof of inbox delivery.

## Verify

In a logged-out private browser open:
https://foodtruckworks.com/teaser?utm_source=instagram&utm_medium=social&utm_campaign=launch_teaser

Realtime should show page_view. Submit using an address that can receive a new confirmation. You should land on /launch-updates/thanks and see launch_signup_requested. Follow the email link and click the confirmation button. The clean thank-you page should emit launch_signup_confirmed. Reload it: no further conversion. Test invalid captcha and an already confirmed address: no conversion. Network requests to Google must contain no email, feedback or confirmation token.

Use public campaign labels containing letters, numbers, underscores and hyphens, up to 100 characters. Only the five standard utm fields are retained; arbitrary query strings and fragments are discarded. Referrers are reduced to origins. Never put personal information in campaign labels.

## Future app tracking

This release tracks public marketing routes only, including signed-in visitors to /teaser, tagged audience_type=signed_in. site_area=marketing separates these events from future site_area=app events. The script deliberately allows only approved routes. Later app integration should use reviewed route names, no raw IDs/query strings, and explicit workflow events. Do not simply move an unrestricted tag into base.html. Configure site_area and audience_type as event-scoped custom dimensions if you want them in reports. signed_in does not mean staff; a separate internal/testing classification needs explicit configuration.

The confirmation page itself never loads this tag. Successful POSTs redirect with 303 to a clean GET; event names only are stored briefly in the existing signed session and consumed once. A page visit and analytics cookies remain subject to your site's analytics consent/privacy settings; this patch does not implement a consent manager.
