# Tip submission window update

Copy the four changed application files and the Alembic revision from this patch.
The migration `4e7c90a81b62` follows `2b9e51c73a04`. Apply it to the intended
database before deploying the application files.

After deployment, a manager must open Tip Settings for each participating store,
choose the first time whose tips have not already been allocated, and save.
Existing store settings have no activation cutoff until that step is completed.
New submissions are blocked until then. The activation cutoff cannot be changed
through Tip Settings after it is saved. The default employee window is 24 hours
from the report range start, configurable from 1 to 720 hours. Owners, managers,
and payroll bypass the rolling employee window but cannot bypass the store's
activation cutoff.

Check a range before activation is rejected, a member's range expires after
the configured number of hours, and payroll can submit that expired range if
it begins after activation. Existing submitted records are not changed.
