# Local PostgreSQL Acceptance

All migration and concurrency acceptance uses a dedicated disposable database
whose name starts with `travel_web_test`. It must not target `travel_agent`,
`travel_web`, or any production database.

Set:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://<local-role>:<local-password>@127.0.0.1:<port>/travel_web_test
DATABASE_URL=$TEST_DATABASE_URL
```

Run the reversible migration sequence:

```bash
uv run alembic upgrade head
uv run alembic downgrade base
uv run alembic upgrade head
```

Production database and role creation remains a separately reviewed deployment
step. A least-privilege operator plan is:

```sql
CREATE ROLE travel_web_api LOGIN PASSWORD '<secret-from-secret-manager>';
CREATE DATABASE travel_web OWNER travel_web_api;
REVOKE ALL ON DATABASE travel_agent FROM travel_web_api;
```

Do not execute this production plan during local implementation.
