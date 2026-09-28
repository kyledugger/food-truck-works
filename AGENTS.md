# Food Truck Works Agent Guide

## Scope

This repository is a server-rendered Poynt reporting application. Do not modify application code unless the user explicitly asks for an implementation change.

## Architecture

Before changing dates, timestamps, schedules, time cards, tip deadlines, or
reports, read `documentation/TIME_CONVENTIONS.md` and follow its storage,
timezone, interval, and daylight-saving rules.

- Runtime: FastAPI served by Uvicorn (`main:app`).
- UI: Jinja2 templates in `templates/`, Bulma via CDN, project CSS in `static/css/app.css`.
- Persistence: SQLAlchemy ORM with PostgreSQL and psycopg.
- Database models: `User`, `Organization`, `OrganizationMember`, `Employee`, `OrganizationInvitation`, and `PoyntConnection`.
- Authentication: Argon2 password hashes and Starlette signed-cookie sessions.
- Organization scoping: `organization_context.py` validates the session organization against the user's memberships.
- Poynt integration:
  - `routers/oauth.py` handles OAuth.
  - `poynt/token.py` exchanges and refreshes tokens.
  - `poynt/client.py` calls Poynt APIs.
  - `poynt/connection.py` persists organization-specific Poynt credentials.
- Feature routers:
  - `routers/auth_routes.py`: registration, login, logout.
  - `routers/employees.py`: employee management and invitations.
  - `routers/poynt.py`: catalog, stores, and the Orders report.

## Orders Report

`GET /poynt/orders` is the main reporting endpoint.

- Requires an authenticated user, an authorized organization, and a Poynt connection.
- Accepts `start`, `end`, and repeated `stores` query parameters.
- Interprets naïve date/time values in `America/Phoenix`.
- Limits report ranges to three days.
- Fetches live Poynt orders, filters stores, separates completed from non-completed orders, and calculates sales, item, timing, tip, SKU/category, and chart data.
- `templates/orders.html` renders the report, Chart.js visualizations, fee calculator, and tip calculator.
- SKU/category/store presentation uses local mappings in `sku_map.py`, `categories.py`, and `devices.py`.

## Local development workflow

Use the repository virtual environment and PowerShell scripts where available.

```powershell
.\run-local.ps1
```

This sets `DOTENV_FILE=.env` and starts:

```powershell
uvicorn main:app --reload
```

The local app is normally available on port 8000. Do not start another instance if port 8000 is already in use.

Useful commands:

```powershell
.\stop8000.ps1
.\.venv\Scripts\python.exe test.py
.\.venv\Scripts\alembic.exe current
```

`test.py` is an ad hoc database-index inspection utility, not an automated test suite.

## Database environments and safety

- `.env` is the local development database configuration.
- `.env.local-prod-db` contains production credentials and connects a locally running application to the production database.
- Treat `.env.local-prod-db`, `.env`, private keys, password files, JWT samples, and database dumps as sensitive. Never expose their contents in responses, logs, patches, or commits.
- Never modify the production database, run migrations against it, delete production data, or execute destructive SQL without the user's explicit approval.
- This rule includes operations launched from a local app using `.env.local-prod-db`.
- Before any database operation that could alter data or schema, identify the exact target environment and explain the intended action.
- Prefer local development data for investigation and testing. Access production data only when the user explicitly asks for production-data investigation or when it is necessary to diagnose a production-specific problem.

## Migrations and schema

- Alembic configuration lives in `alembic.ini` and `alembic/env.py`.
- Application startup calls `Base.metadata.create_all()`, so schema creation may occur outside Alembic.
- The migration history and local schema artifacts may not fully align with current ORM models. Inspect the active environment and repository state before proposing schema changes.
- `alembic/env.py` loads `.env` directly and does not automatically follow `DOTENV_FILE`; do not assume migration commands use the same environment as the application launcher.
- Do not run `alembic upgrade`, generate migrations, or otherwise alter a database unless the user has explicitly authorized the target environment and operation.

## Testing expectations

- There is no established automated test suite.
- For code changes, add or update focused tests where practical.
- At minimum, run relevant syntax/import checks and targeted manual verification.
- For report changes, verify unauthenticated behavior, no-Poynt-connection behavior, date validation, store filtering, empty results, and completed versus cancelled order handling.
- Do not use production data for testing or write tests that require production credentials.

## Repository hygiene

- Preserve existing user changes and do not revert or overwrite unrelated work.
- Prefer `rg` for repository searches.
- Use `apply_patch` for edits.
- Do not commit secrets or ignored environment/database artifacts.
- Documentation under `routers/documentation/` describes prospective event-management design; it is not part of the current application runtime.
- Never commit, push, merge, reset, rebase, or otherwise alter Git history unless the user explicitly requests it.
- Before making changes, inspect `git status` and preserve all pre-existing modifications and untracked files.
- Do not modify `.env`, `.env.local-prod-db`, private keys, password files, database dumps, or other credential-bearing files unless the user explicitly requests it.
- When proposing changes that affect database models, migrations, authentication, Poynt credentials, or production configuration, explain the potential impact before implementation.
- Prefer the smallest change that solves the requested problem. Do not refactor unrelated code unless explicitly requested.
