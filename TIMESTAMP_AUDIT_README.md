# Read-only timestamp audit

From the project root with the virtual environment active:

```powershell
python scripts/audit_timestamps.py --env-file .env
```

The script identifies the host and database name, inspects application
timestamp columns, and prints counts, earliest/latest values, and at most three
recent row IDs and timestamps per column. It does not print connection secrets,
names, emails, tokens, or tip amounts. It sets the PostgreSQL transaction to
READ ONLY before inspecting data and makes no changes. Review the host/database
line before sharing the output so we know which environment was audited.

For production, run only when you deliberately choose its environment file;
do not paste the environment file itself. A local audit establishes the local
database's state only. The output is evidence for a separate migration; it
does not prove every historical timestamp has UTC meaning on its own.
