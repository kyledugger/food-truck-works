# Kitchen marker picker

Extract into the existing FTW project root, deploy, then reload kitchen tablets. Apply after the food icon/admin test mode and 10% readability updates. No database migration is required.

Each rule now has a Select marker button showing its current marker. It opens a separate modal with built-in food icons, grouped food/drink emojis, and a field to paste another emoji. Select a choice to apply it to that rule and return to the editor. Cancel or Escape leaves the selection unchanged. Save in the main editor persists all rules as before.

Matching targets, matching text, previews, removal, shared organization scope, revision handling, and existing emoji/icon data formats remain unchanged. Icon dimensions and the kitchen readability adjustment remain unchanged. Emoji appearance and support depend on the tablet's fonts and operating system.

Validation: JavaScript syntax and existing callout checks passed. DOM interaction checks covered icon and emoji selection, switching types, over 100 curated emoji choices, pasted custom markers, cancelling, preview updates, and the saved payload/revision. Native browser screenshots were not available in this environment.

Includes only kitchen-display.js, kitchen-markers.css, both kitchen-related templates, and these notes. Merge template changes if you have edited those files independently since the last update.
