# Oracle SQL Workbench

This Django application serves the Oracle SQL*Plus-style compiler and editor. The root URL and `/oraclesql/` open the workbench; its `/api/*` endpoints handle Oracle connections, SQL execution, object browsing, and saved SQL files.

## Start on Windows

1. Create or activate a Python virtual environment.
2. Install dependencies: `python -m pip install -r requirements.txt`.
3. Configure `.env` with `HOST`, `PORT`, `TLS_KEY_FILE`, and `TLS_CERT_FILE` if you need HTTPS/network access. Without TLS the server only binds to loopback.
4. Start the app: `python run_server.py` (or `npm start` if you prefer the existing service shortcut).

The Windows service launcher opens `https://JEET-PC:3000/oraclesql/`. Trust the configured certificate on each client. Do not expose the development WSGI server directly to the public internet.

## Oracle SQL workbench

Oracle credentials remain in server memory for the active browser session, use an HTTP-only session cookie, and expire idle Oracle sessions after 30 minutes. `.sql` files remain isolated by Oracle account, database, and browser workspace in `sql-files/`. The workbench executes statements with the connected Oracle user's database permissions.

## Development and production

`run_server.py` is a threaded Django WSGI runner intended for this local Windows workstation workflow. For internet-facing production, deploy Django behind a maintained production WSGI/ASGI server and HTTPS reverse proxy, set `DEBUG=False`, use a strong `DJANGO_SECRET_KEY`, configure `DJANGO_ALLOWED_HOSTS`, backups, and review Django's deployment checklist. The previous `server.js` is retained as a source snapshot but is no longer used by the normal startup path. `npm start` and the service launcher start Django.
