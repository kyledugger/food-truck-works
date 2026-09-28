# Organization store setup patch

Apply the included files to the matching paths in your current project, then run:

```powershell
alembic upgrade head
```

Check the target database environment before running Alembic. The new revision is
`6a18b9e4c532` and follows `4e7c90a81b62`. Restart the application and open
Dashboard > Store Settings. Poynt must already be connected. Save each Poynt
store with its IANA timezone before opening its Tip Settings.

If the application started before Alembic, `Base.metadata.create_all()` may
have created `organization_stores` already. The corrected migration checks the
existing table's columns, primary key, unique constraint, and foreign key, then
records the revision. Do not drop the table or stamp the revision to bypass it.

Tip payout policy, cash confirmation, and employee submission window remain
separate for every organization and store. Existing tip settings and cutoff
timestamps are not changed by this migration. Store names now come from the
organization's configuration; the former Ice Cream Crush name mapping is not
used by the Orders report.

This setup patch does not yet change Orders report date-range interpretation,
the tip submission report's definition of today, or browser display formatting.
Those need the broader UTC/date migration after stores are configured.
