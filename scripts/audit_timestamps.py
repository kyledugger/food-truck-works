"""Read-only inventory of database timestamps before a UTC schema migration.

Usage: python scripts/audit_timestamps.py --env-file .env
This script never imports the app or calls create_all().
"""
import argparse
import json
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import DateTime, create_engine, inspect, text
from sqlalchemy.engine import make_url


TABLES = (
    "users", "user_security_tokens", "organizations", "organization_members",
    "employees", "organization_invitations", "poynt_connections",
    "tip_submissions", "tip_store_settings", "tip_order_claims",
    "tip_employee_payouts",
)


def run(env_file: Path):
    if not env_file.is_file():
        raise SystemExit(f"Environment file not found: {env_file}")
    database_url = dotenv_values(env_file).get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is missing from the selected environment file")
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise SystemExit("This audit is for PostgreSQL only")
    engine = create_engine(url.set(drivername="postgresql+psycopg"), pool_pre_ping=True)
    report = {"database": {"host": url.host, "name": url.database}, "tables": {}}
    try:
        with engine.connect() as connection:
            with connection.begin():
                # The first statement makes PostgreSQL reject any attempted write.
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                connection.exec_driver_sql("SET LOCAL statement_timeout = '15s'")
                report["database"]["session_timezone"] = connection.scalar(text("SHOW TIME ZONE"))
                report["database"]["alembic_revision"] = connection.scalar(
                    text("SELECT version_num FROM alembic_version LIMIT 1")
                )
                inspector = inspect(connection)
                available = set(inspector.get_table_names())
                quote = connection.dialect.identifier_preparer.quote
                for table in TABLES:
                    if table not in available:
                        report["tables"][table] = {"status": "missing"}
                        continue
                    columns = inspector.get_columns(table)
                    names = {column["name"] for column in columns}
                    entries = {}
                    for column in columns:
                        if not isinstance(column["type"], DateTime):
                            continue
                        name = column["name"]
                        field = quote(name)
                        relation = quote(table)
                        row = connection.execute(text(
                            f"SELECT count(*) AS total, count({field}) AS populated, "
                            f"min({field}) AS earliest, max({field}) AS latest FROM {relation}"
                        )).mappings().one()
                        identifier = "id" if "id" in names else None
                        sample_sql = (f"SELECT {quote(identifier)}, {field} FROM {relation} "
                                      f"WHERE {field} IS NOT NULL ORDER BY {field} DESC LIMIT 3"
                                      if identifier else
                                      f"SELECT {field} FROM {relation} WHERE {field} IS NOT NULL "
                                      f"ORDER BY {field} DESC LIMIT 3")
                        samples = [list(result) for result in connection.execute(text(sample_sql))]
                        entries[name] = {
                            "sql_type": str(column["type"]),
                            "total_rows": row["total"], "populated_rows": row["populated"],
                            "earliest": row["earliest"], "latest": row["latest"],
                            "latest_samples_id_and_time": samples,
                        }
                    report["tables"][table] = entries
    finally:
        engine.dispose()
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path,
                        help="Explicit environment file, usually .env for the local database")
    args = parser.parse_args()
    run(args.env_file)
