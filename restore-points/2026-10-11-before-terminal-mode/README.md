# OraPlay Oracle SQL Workbench

## Start

Run `npm install` once, then `npm start` and open `http://127.0.0.1:3000`. The app opens on a full-screen Oracle login gate. The username is remembered on that browser after a successful login; passwords are never saved by the app and are cleared after login. Each login receives a random, HttpOnly session cookie. Oracle credentials and SQL*Plus connections are isolated in server memory per login; sessions expire after 30 minutes idle. Logging out closes that login's SQL*Plus connection and returns to the login screen. SQL files are isolated by Oracle account, database, and browser workspace under `sql-files/`; they are available again on that browser after reconnecting.

The app runs statements with the connected Oracle account's permissions. Use separate Oracle accounts for separate people; if people share one database account, Oracle treats them as the same database identity. The server listens on loopback by default. To allow other devices on a trusted LAN, configure `TLS_CERT_FILE` and `TLS_KEY_FILE` in `.env` with a certificate trusted by those devices, set `HOST=0.0.0.0`, and allow the chosen port only on the Windows private-network firewall profile. The server refuses non-loopback HTTP binds. Do not expose this development server directly to the internet.
## Workbench features

- Lists accessible application-schema objects, including objects outside the connected user's own schema. Select one to inspect its columns; search by schema or object name and drag the divider to resize the panel.
- Saves `.sql` files in `sql-files/`. The default unsaved file is `query.sql`; Save writes directly to the current filename, while Open lists saved files in a scrollable picker.
- **Run Statement** runs the selected statement (or the statement at the cursor); **Run Script** runs every statement in the editor in order.
- Download saves the current editor text as a `.sql` file. Share Code uses the system share dialog when available or copies SQL to the clipboard.
- Convert Case preserves quoted string literals, quoted identifiers, and comments.
- Change editor font size, switch between dark and light themes, log out of Oracle, or expand the workspace to full screen.

The Oracle driver uses Thin mode, so Oracle Instant Client is not required. Query results are capped at 500 rows. Online compiler statements are auto-committed; SQL*Plus has its own persistent Oracle connection and defaults to `AUTOCOMMIT OFF`, so `COMMIT` and `ROLLBACK` apply to that login's transaction. PL/SQL `DBMS_OUTPUT` appears in the Output tab.
