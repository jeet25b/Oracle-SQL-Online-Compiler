# OraPlay Django Learning Workbench

The site now runs on Django. The existing SQL*Plus-like workbench and Editor are served by Django templates/static handling, and their `/api/*` endpoints execute Oracle operations through `python-oracledb`. LMS accounts and learning records use Django models, built-in authentication, and Django Admin.

## Start on Windows

1. Create or activate a Python virtual environment.
2. Install dependencies: `python -m pip install -r requirements.txt`.
3. Configure `.env` with `HOST`, `PORT`, `TLS_KEY_FILE`, and `TLS_CERT_FILE` if you need HTTPS/network access. Without TLS the server only binds to loopback.
4. Create the Django tables: `python manage.py migrate`.
5. Start the app: `python run_server.py` (or `npm start` if you prefer the existing service shortcut).

The existing service launcher now starts the Django server. It still opens `https://JEET-PC:3000`; trust the configured certificate on each client. Do not expose the development WSGI server directly to the public internet.

## LMS accounts and records

Create a Django administrator account with `python manage.py createsuperuser`, open `/admin/`, and add users with the Admin, Teacher, or Student role. The Admin role grants Django Admin access. `/portal/login/` signs in portal users and `/portal/dashboard/` shows the role-specific landing page. Login and logout create attendance records.

Django uses a local SQLite database for LMS records by default. To use a dedicated Oracle LMS schema, set `LMS_DB_ENGINE=oracle`, `LMS_ORACLE_DSN`, `LMS_ORACLE_USER`, and `LMS_ORACLE_PASSWORD` in `.env`, then run migrations against that account. Keep the LMS account separate from learner SQL execution accounts.

## Oracle SQL workbench

The compiler keeps Oracle credentials only in server memory for the active login, uses an HTTP-only session cookie, and expires idle Oracle sessions after 30 minutes. `.sql` files remain isolated by Oracle account, database, and browser workspace in `sql-files/`. The workbench executes statements with the connected Oracle user's database permissions; give student accounts restricted privileges and avoid connecting the compiler to the LMS owner schema.

## Development and production

`run_server.py` is a threaded Django WSGI runner intended for this local Windows workstation workflow. For internet-facing production, deploy Django behind a maintained production WSGI/ASGI server and HTTPS reverse proxy, set `DEBUG=False`, use a strong `DJANGO_SECRET_KEY`, configure `DJANGO_ALLOWED_HOSTS`, backups, and review Django's deployment checklist. The previous `server.js` is retained as a source snapshot but is no longer used by the normal startup path. `npm start` and the service launcher start Django. If the old server is already running, choose **Restart Service** in the launcher to switch to Django; restarting closes active Oracle sessions, so commit or roll back pending SQL first.
