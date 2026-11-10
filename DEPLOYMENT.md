# Deployment and recovery

For a multi-worker deployment that preserves interactive Oracle sessions, see
`ORACLE_EXECUTION_SERVICE.md`.

The normal Windows launcher remains suitable for the existing local lab setup.
The application does not change its current certificate, port, theme, or account
permissions when production options are added.

## Before an update

1. Run the relevant Python and JavaScript tests and `python manage.py check`.
2. Create an encrypted backup to a private location outside the source repository:
   `python manage.py backup_portal --output C:\PrivateBackups\portal-2026-10-31.backup`
3. Verify it independently with
   `python manage.py backup_portal --verify C:\PrivateBackups\portal-2026-10-31.backup`.
4. Apply reviewed migrations, then restart the workbench during a maintenance
   window after users commit or roll back database work. Live Oracle connections
   are process-local; restarting closes them.
5. Check sign-in, role access, scheduled task visibility, task submission, review,
   saved connection selection, and both color modes. Confirm new assets return 200.

Backups contain portal records (including encrypted credentials) and uploaded
media, encrypted with a key derived from `DJANGO_SECRET_KEY`. They intentionally
exclude active login sessions. Protect the application secret separately; keep
it available to authorized recovery operators. A backup made with a different
secret cannot be decrypted by this command. Oracle practice schema data needs a
separate database backup managed by its database administrator.

`DB_CREDENTIALS_ENCRYPTION_KEY` can be set to a separate private random value
for newly stored Oracle, email, and temporary-schema credentials. Existing
credentials remain readable using `DJANGO_SECRET_KEY` as a fallback. Keep the
original Django secret until those records are re-encrypted; do not rotate it
immediately after setting the new key. The backup archive still uses the
Django secret independently.

Verification checks integrity, decryption and fixture structure; it does not
prove a full recovery. Regularly rehearse restoration into a separate, empty
database with the same application version and secret: apply migrations, decrypt
the archive in a controlled environment, load `portal.json` using Django's
`loaddata`, restore the `media/` files, then run the workflow checks above. Never
test a restore over the live database. Remove temporary decrypted material after
the rehearsal using the organization's approved data handling procedure.

## Public hosting

Use a maintained production WSGI/ASGI server and HTTPS reverse proxy rather than
the local runner. Preserve the certificate trust and network controls required
by the deployment. Set explicit hosts, a private random secret and `DEBUG=False`.

The optional environment profile `WORKBENCH_DEPLOYMENT=production` requires
those settings, enables HTTPS redirects by default and requires secure cookies.
Keep `SECURE_HSTS_SECONDS=0` until HTTPS and certificate trust have been verified
for every intended client. Increase it deliberately after that validation;
subdomain coverage is separately opt-in. HSTS preload remains off. Do not enable
proxy-header trust unless the proxy strips untrusted incoming headers.

Run `python manage.py check --deploy` with the actual production environment.
Monitor failed requests and backup verification, keep secrets out of logs and
version control, and retain a known-good application version for rollback.
