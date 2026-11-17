# Built-in kitchen food icons: first release

This update adds the approved chocolate-dipped vanilla ice cream bar artwork to a shared FTW food icon library. The library is available to all organizations; each organization's matching rules remain private and shared only across its own stores.

## Install

Extract the files into the existing FTW project root, preserving folders, and deploy. No database migration, environment variable, provider registration, or Poynt API change is required. Existing marker rules are stored as JSON, which can hold the new icon identifier without a schema change.

Reload any already-open kitchen tablets after deployment so they receive the new JavaScript. Template asset version markers have been updated. The included PNG and `kitchen_icons.py` must deploy along with the router, stylesheet and JavaScript.

## Add a rule

1. As an owner or manager, open the kitchen queue.
2. Open **⋯ → Kitchen item markers**.
3. Add a rule or edit an existing one. Choose **Item name** or **Modifier value**, and enter text appearing in the product names you want to match, such as `bar`.
4. Set **Marker type → Food icon**, then select **Ice cream bar**. The selected artwork has a green outline and appears in the live preview.
5. Save. The icon appears before matching text in the queue and is also visible on read-only queue screens. Existing items are updated without changing their preparation state.

Rules match anywhere, ignoring case; modifier underscores match spaces. Each rule uses either an emoji or a food icon. Separate matching rules can show both, and duplicate matching rules do not repeat the same icon next to the same text. No rules are automatically created for any organization.

## Appearance

The queue displays the image at 26 pixels beside item names and 22 pixels beside modifier values. Item names, quantities and modifier text remain readable independently of the image. If an asset cannot load, the image is removed and the item text remains.

Open `FOOD_ICON_PREVIEW.html` locally to compare the approved artwork at 24, 26 and 28 pixels on white and gray backgrounds. The original transparent PNG is bundled unchanged; this release does not add image uploads or an image editor.

## Growing the library

The server owns the catalog in `kitchen_icons.py`. Rules save the stable ID `ice-cream-bar`, not a URL or file path. To add more approved artwork later, include a static asset and add an ID/label/URL catalog entry. The existing picker reads that catalog automatically. Keep IDs stable when improving artwork, and version asset filenames to refresh cached images.

Only catalog IDs are accepted by the save endpoint. External URLs and file paths cannot be supplied through matching rules. Owner/manager permissions, CSRF checks, revision conflict handling and organization isolation remain in force. No additional Poynt requests are made.

## Validation

All 228 kitchen regression tests passed, including new mixed emoji/icon round trips and validation. DOM integration checks using real route response fixtures verified item/modifier matching, deduplication, 26-pixel image dimensions, type switching, previews, saving and read-only rendering. JavaScript syntax and existing customer-callout checks passed. No production database or live queue was modified during development.
