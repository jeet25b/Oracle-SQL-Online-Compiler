# Dedicated Oracle execution service

The optional service deployment keeps live SQLCL and workbook Oracle connections in
one long-running process. Public web workers forward compiler and API requests to
that process; they never create a second local copy of an interactive session.
The existing single-process Windows launcher is unchanged when the service
environment variables are absent.

## Configuration

Use the same source version, `DJANGO_SECRET_KEY`, portal database, and other Django
settings for the service and every public web worker. Generate one private random
`ORACLE_SERVICE_TOKEN` of at least 32 characters and set it identically in both.
Do not put this token in source control. The service must listen only on
`127.0.0.1`; do not expose port 3001 through the firewall or reverse proxy.

Start exactly one owner process before starting the public workers:

```powershell
$env:ORACLE_SERVICE_TOKEN = '<private-random-token-at-least-32-characters>'
$env:ORACLE_SERVICE_PORT = '3001'
python run_oracle_service.py
```

Set these variables for each public WSGI worker (and **do not** set
`ORACLE_SERVICE_MODE` there):

```powershell
$env:ORACLE_SERVICE_TOKEN = '<the-same-private-token>'
$env:ORACLE_SERVICE_URL = 'http://127.0.0.1:3001'
```

Launch public workers with `oraplay.wsgi:application`. The gateway forwards
`/api/*`, `/oracle-sql/`, and `/oraclesql/workspace/` to the owner. Other pages
remain on the public workers. The owner uses the same Django session ID and
authenticates every request through the normal application middleware. The
internal token is checked before Django handles a forwarded request. If the owner
is unavailable, the gateway returns HTTP 503; it does not silently create an
unrelated Oracle session in a public worker.

The existing Windows launcher also loads this gateway when `ORACLE_SERVICE_URL`
is set. Start the owner first, then run `run_server.py` with the public TLS
configuration and the gateway variables above. One public runner process is
still sufficient for the local lab; a production reverse proxy may distribute
non-compiler pages across multiple public WSGI workers.

## Operations

- Keep exactly one owner process for this deployment. Multiple owners would
  recreate the original process-local session split.
- Restart public workers independently. Their Oracle sessions remain in the
  owner. Restarting the owner closes its live connections; saved credentials can
  establish fresh sessions, but uncommitted Oracle work is lost.
- During an update, ask users to commit or roll back before restarting the owner.
- Monitor the owner process and the gateway's 503 responses. Restart the owner
  before public workers after a full outage.
- Check `GET http://127.0.0.1:3001/__oracle_service/health` from the host with
  the private `X-Oracle-Service-Token` header; it returns `ok` when the process
  is accepting requests. This checks the process, not an Oracle login.
- The owner is a threaded server, so the existing per-session operation locks
  continue to protect interactive connections.

This design isolates the existing stateful execution implementation behind a
process boundary. It does not turn live Oracle connections into Django session
data or pool them per SQL statement.
