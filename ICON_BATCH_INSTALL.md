# Food icons and administrator kitchen simulation

Extract into the FTW project root, preserving folders, then deploy and reload kitchen tablets. No database migration is required.

The shared picker now contains six icons: ice cream bar, frozen banana, shave ice, ice cream taco, iced coffee, and soda drink. Create matching rules through More > Kitchen item markers > Food icon. Existing rules remain unchanged; new rules are not automatically added.

Kitchen test mode is now restricted to FTW administrators listed in the existing `LAUNCH_ADMIN_USER_IDS` setting. Make sure your personal user ID is included there. If the setting is empty, no one can use test mode. Ordinary subscriber owners/managers and store-display logins cannot open test mode or use its queue, actions, or event stream. Normal kitchen access is unchanged. Admins must also have owner/manager access to the selected organization. Use your personal administrator login on any tablet used for development testing.

The simulator draws from the 68 product names/SKUs supplied from your pricing page. It mixes categories, uses quantities 1–3, and retains the existing topping, drink-option and shave-ice modifier examples. It does not import the pricing page's review warnings or change prices/catalogs. Ice cream tacos are selectable artwork, but not simulated menu items because they were absent from the supplied product list.

Test tickets remain separate from live tickets, sales, tips, and provider orders. Large orders still contain 12 lines. Open FOOD_ICON_PREVIEW.html for an artwork preview.

This package is cumulative with the previous food icon and quantity badge update. Existing files in the ZIP replace their namesakes. It also includes the kitchen router and live dashboard router from the source supplied in this conversation, including prior payment and transaction-trigger work; merge carefully if you have edited those files since then.
