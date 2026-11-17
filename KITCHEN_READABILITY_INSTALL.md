# Kitchen queue readability

Extract into the project root and deploy, then reload kitchen tablets. This small patch goes on top of the food-icon/admin-test-mode update. No migration is needed.

Kitchen cards grow from 290px to 319px. Text, emojis, row heights, spacing, and quantity circles increase approximately 10%. Food images remain exactly 26px beside item names and 22px beside modifiers. Narrow-screen cards still fit the viewport; larger cards mean fewer fit across at once. Toolbar buttons can wrap.

The template changes the board stylesheet version so browsers fetch the update. Includes only the board CSS, kitchen template, and these notes.
