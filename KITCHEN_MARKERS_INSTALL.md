# Kitchen item markers

Apply the files in this patch, then run `alembic upgrade head` against your intended database before starting the updated application. This adds the `kitchen_markers` table; no existing products or tickets are changed. This patch follows revision `f9c4ea60b238`.

Owners and managers can open a working kitchen queue and choose **⋯ → Kitchen item markers**. Rules are shared across every store in the organization. Use **Add rule**, select **Item name** or **Modifier value**, enter text to match, and paste an emoji. Save applies to existing and new tickets, including read-only and test queues. Remove a row and save to delete a rule; removing every row disables markers.

Examples:

| Target | Text | Marker |
| --- | --- | --- |
| Item name | banana | 🍌 |
| Modifier value | caffeine free | ⚠️ |
| Modifier value | strawberry | 🍓 |
| Modifier value | coconut | 🥥 |

Matching ignores case, treats underscores as spaces, and finds text anywhere in the name or individual modifier value. The marker is always prefixed to the full matching name/value. Multiple matching markers follow rule order; duplicate markers appear once per name/value. Modifier headings and order notes are not matched. Rules affect presentation only; they do not change POS data, ticket identity, or reports.

There are no default rules. Up to 50 rules are supported. If another manager changes the rules while an editor is open, close and reopen it before saving. Emoji appearance varies by device.

Validation: 40 isolated backend tests passed, including role/scope/CSRF checks, optimistic revision handling, shared live/test/read-only settings, and migration upgrade/downgrade. Mocked browser checks verified prefix placement, per-value flavor markers, duplicate suppression, editor controls, and read-only behavior. No production database or provider writes were performed.
