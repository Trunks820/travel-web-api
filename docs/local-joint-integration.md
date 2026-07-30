# J0.5 Local Joint-Integration Runbook

Status: **J0.5 BFF Integration Preparation**

This runbook is only for disposable local acceptance. It must never connect to
`travel_web`, `travel_agent`, or another production database. It does not
authorize deployment or sibling-repository edits.

## 1. Fixed local topology

```text
travel-web       http://localhost:3000
  same-origin /api proxy
travel-web-api   http://127.0.0.1:6670
  private HTTP
hermes-travel    http://127.0.0.1:6666

PostgreSQL       127.0.0.1:55432
database         travel_web_test_joint
```

The frontend and Hermes processes remain owned by their separate development
windows. Before starting Hermes, that window must independently prove that its
own database and provider dependencies are local/test resources. This BFF
runbook does not provide or authorize a production Hermes configuration.

## 2. Start disposable PostgreSQL

The existing `.local-postgres/` directory is a protected, ignored local asset.
Do not reinitialize or delete it.

In PowerShell terminal A:

```powershell
uv run python scripts/local_joint_postgres.py serve
```

The helper resolves only this repository's existing `.local-postgres` data
directory and starts PostgreSQL with:

```text
host = 127.0.0.1
port = 55432
```

`serve` keeps the owner process open. `Ctrl+C` performs `pg_ctl stop -m fast
-w`. A second terminal can also request a graceful stop:

```powershell
uv run python scripts/local_joint_postgres.py stop
uv run python scripts/local_joint_postgres.py status
```

Do not terminate `postgres.exe` directly.

## 3. Set local-only BFF environment

In PowerShell terminal B, generate new process-local secrets. Do not put these
values in `.env.example`, source control, chat, or screenshots.

```powershell
$pepperBytes = [System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
$hermesBytes = [System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
$hermesAdminBytes = [System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)

$env:LOCAL_JOINT_MODE = "1"
$env:APP_ENV = "development"
$env:DATABASE_URL = "postgresql+asyncpg://postgres@127.0.0.1:55432/travel_web_test_joint"
$env:TEST_DATABASE_URL = $env:DATABASE_URL
$env:USER_ORIGIN = "http://localhost:3000"
$env:ADMIN_ORIGIN = "http://localhost:3001"
$env:COOKIE_SECURE = "false"
$env:HERMES_BASE_URL = "http://127.0.0.1:6666"
$env:HERMES_READ_TIMEOUT_SECONDS = "90"
$env:SECRET_HASH_PEPPER = [Convert]::ToHexString($pepperBytes).ToLowerInvariant()
$env:HERMES_INTERNAL_CREDENTIAL = [Convert]::ToHexString($hermesBytes).ToLowerInvariant()
$env:HERMES_BFF_INTERNAL_ADMIN_CREDENTIAL = [Convert]::ToHexString($hermesAdminBytes).ToLowerInvariant()
$env:DIRECTMAIL_ACCESS_KEY_ID = ""
$env:DIRECTMAIL_ACCESS_KEY_SECRET = ""
```

The local harness refuses to start unless:

- `APP_ENV` is `development` or `test`
- `LOCAL_JOINT_MODE=1`
- PostgreSQL is loopback and the database starts with `travel_web_test`
- `USER_ORIGIN` is exactly `http://localhost:3000`
- `COOKIE_SECURE=false`
- Hermes is loopback port `6666`
- the Hermes read timeout is at least 45 seconds, leaving three keepalive
  intervals of margin over Hermes's 15-second SSE keepalive; the recommended
  local and production default is 90 seconds
- all local secrets are independently generated values of at least 32
  characters
- DirectMail credentials are empty

It fails closed for `APP_ENV=production`.

## 4. Create, migrate, and reset the disposable database

Create the database once:

```powershell
uv run python scripts/local_postgres.py --host 127.0.0.1 --port 55432 --database travel_web_test_joint
```

Upgrade and check:

```powershell
uv run alembic upgrade head
uv run alembic check
```

Reset all BFF schema state between joint-integration runs:

```powershell
uv run python scripts/reset_local_joint_database.py
```

The reset script validates the same local-only guard before running:

```text
alembic downgrade base
alembic upgrade head
```

It cannot target a non-loopback host or a database whose name does not start
with `travel_web_test`.

## 5. Seed one single-use local Invitation

With the guarded environment and migrated database:

```powershell
uv run python scripts/seed_local_invitation.py
```

The command prints exactly one local acceptance value:

```text
LOCAL_ONLY_INVITATION=inv_<opaque-random-value>
```

Only its purpose-bound hash is stored in PostgreSQL. The script creates no
Administrator route or shared multi-use code. Run it again for another person;
each output remains independently single-use.

## 6. Start the BFF with the local OTP harness

After the Hermes window has safely started its local service on port `6666`:

```powershell
uv run python scripts/run_local_joint_bff.py
```

The process listens only on `127.0.0.1:6670`. For a send-code request it writes
the local OTP to this terminal:

```text
LOCAL_ONLY_OTP purpose=EMAIL_AUTH code=<six-digits>
```

Use that code with the browser's existing `challenge_id`. The harness does not
persist raw OTP values and exposes no OTP-reading HTTP endpoint. Registration,
returning login, attempt limits, expiry, single use, purpose binding, Session
cookies, and Invitation consumption still execute through the real BFF code
and disposable PostgreSQL transactions.

This console mailer exists only in `scripts/run_local_joint_bff.py`; the normal
`src.app:app` runtime still uses DirectMail. The harness refuses production and
also refuses configured DirectMail credentials.

To exercise real DirectMail instead, do not use the harness. Start the normal
app with separately supplied local-development DirectMail credentials:

```powershell
uv run uvicorn src.app:app --host 127.0.0.1 --port 6670
```

Never commit those credentials.

## 7. Frontend joint-integration expectations

The `travel-web` development server owns the port-3000 proxy. Its browser
origin must remain `http://localhost:3000`, and `/api/*` must proxy to
`http://127.0.0.1:6670` without rewriting Cookie or SSE semantics.

For an SSE `interrupted` event, including one emitted after upstream clean EOF
without `complete` or `failed`, the browser must close EventSource and begin
authenticated job polling. It must not render a business failure or claim that
quota was released until polling returns a true terminal status.

## 8. Shutdown and cleanup

1. Stop `travel-web` and the BFF with their normal `Ctrl+C` handlers.
2. Stop Hermes through its owning window.
3. Optionally reset the disposable BFF schema.
4. Gracefully stop PostgreSQL:

```powershell
uv run python scripts/local_joint_postgres.py stop
```

5. Confirm:

```powershell
uv run python scripts/local_joint_postgres.py status
```

Expected output:

```text
LOCAL_POSTGRES_STATUS=stopped
```
