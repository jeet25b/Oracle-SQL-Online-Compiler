# Online Oracle Compiler

This is the new Teacher/Student portal frontend. Its files are kept separate from the existing Oracle workbench UI.

## Open the portal

Run the existing Django application from the project root, then visit:

```text
https://JEET-PC:3000/portal/
```

Serve the page through Django. Opening `index.html` directly with `file://` will not authenticate because sign-in requires the server session and CSRF protection.

## Included

- Responsive sign-in page with account-based Teacher and Student routing.
- Ten selectable professional accent palettes on dashboards. Teachers can set the shared site default; users can save a personal color on their device. Violet (`#6D28D9` for light surfaces, with `#A78BFA` as its bright accent) remains the initial default.
- Login accepts an account ID, email, or saved mobile number and determines Teacher or Student access from the authenticated account role.
- Students can self-register; the administrator chooses which profile fields are required under **Portal settings**. Password is always required. Registrations wait for administrator approval before sign-in is enabled. Teachers are created by an administrator.
- Student account IDs use `SDDMMYYNNNN` (for example, `S1710260001`); teacher IDs use `TDDMMYYNNNN`. The four-digit sequence increments each day and is shown after successful registration.
- Dark mode is the first-visit default. A saved Light/Dark choice is remembered on the device; the sign-in page and dashboards include a mode toggle.
- Server-side authentication against `PortalUser`, role enforcement, approved-account checks, and session-based sign-in.
- Separate Teacher and Student dashboards backed by the existing course, enrollment, assignment, test, announcement, and submission records.
- Server-side sign-out that closes active Oracle workspace connections associated with the session.
- Keyboard help is available from the authenticated dashboards; `?` opens it, `Esc` closes it, and `Tab` moves between controls.
- Frontend assets and templates live in this folder; the backend route integration is in `workbench/portal_views.py` and `oraplay/urls.py`.

## Important boundary

The portal sign-in authenticates a Teacher or Student account. It does not ask learners to store or submit raw Oracle credentials. The existing Oracle workbench still has its own connection flow; making workspace access honor the portal session and role is a separate integration step.

## Administrator setup

Run `python manage.py createsuperuser` from the project root to create the protected first administrator. Open `/admin/` on the configured site and sign in with that account. In **Portal users**, use **Add portal user** and select **Teacher** to create a teacher; the system assigns a Teacher ID automatically. In **Portal settings**, choose the required student registration fields and save. Student sign-ups appear with pending approval status; approve them by changing their status to **Approved**. Superuser accounts cannot be deleted through Django's admin or ORM, and their superuser status cannot be removed through a normal model save.

## Design decisions carried forward

- Keep each surface’s layout rules scoped to its own page and component. Avoid broad CSS selectors that unintentionally affect other tabs.
- Use one semantic focus outline per control, and size the control’s layout box to contain its text and cursor. Do not solve spacing issues by hiding focus indicators.
- Keep visual states explicit and test the same interaction in normal and full-screen layouts before calling it done.
- Use native keyboard behavior where possible; document shortcuts and keep mouse operation available.
- The backend resolves the account role from its stored record and verifies approval status before opening a dashboard.


## Shared login autofill policy

`LoginAutofillPolicyMiddleware` loads `login-autofill.js` on Django HTML pages,
including future modules and the admin login. Login forms with a username field
and an existing-password field start empty. Native saved-login hints are enabled
after three typed username characters. Reopening a login dialog or revealing a hidden login form resets the fields
and threshold again. Oracle login dialogs follow the same policy. Registration
and password reset forms retain their separate behavior. No browser passwords are read
or stored by this policy. Browsers control their password-manager popup and may
ignore autocomplete hints, so its exact timing cannot be guaranteed by the site.
