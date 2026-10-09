# Security

## Reporting a problem

Do not open a public issue. Email the project owner directly with what you found and how to reproduce it.

## What is in place now (chunks C00 and C01)

| Area | Control | Proven by |
| --- | --- | --- |
| Secrets | Read from environment only; app refuses to start with a short or repetitive key, wildcard origins or hosts, a non-MySQL URL, or (in production) non-HTTPS origins. `.env` is git-ignored; CI scans every push for committed secrets. | `api/tests/test_config.py`, gitleaks job |
| Logs | Every log line, including stack traces and library logs, passes through a redactor for PAN, GSTIN, email, long numbers and tokens. Request logs never include the query string. | `api/tests/test_logging.py`, `test_security.py` |
| Responses | Security headers on every response including errors; no server banner; API docs off in production; generic 500s with a request id; validation errors never echo submitted values. | `api/tests/test_security.py` |
| Requests | Host allow-list, exact-origin CORS, request-size limit (also for chunked bodies), request-id sanitising. | `api/tests/test_security.py` |
| Database | MySQL strict mode, UTC session, UTC-only timestamps (naive values refused), utf8mb4. | `api/tests/test_migrations.py` |
| Web app | Strict CSP (scripts from our own files only), no raw-HTML injection allowed by lint, no tokens in browser storage, service worker never touches `/api` or non-GET requests. | `web` lint, build, `deploy/nginx` |
| Dependencies | Exact pins, `pip-audit` and `npm audit` fail the build on known vulnerabilities; weekly Dependabot. | CI |
| Network | Dev services listen on 127.0.0.1 only. | `scripts/dev-up` |
| Passwords | Argon2id; at least 12 characters, no composition rules, common and personal passwords refused; the same effort is spent on unknown accounts so they are not faster to reject. | `api/tests/test_passwords.py`, `test_login.py` |
| Guessing | Account locks for 15 minutes after 5 wrong passwords; one address is limited to 20 failures per 15 minutes; every failure looks the same to the caller. | `api/tests/test_login.py` |
| Sessions | Random cookie, only its hash is stored; HttpOnly, SameSite=Lax, `__Host-` prefix and Secure in production; 12-hour limit; ended on sign-out, disable, password reset and password change. | `api/tests/test_login.py`, `test_sessions.py` |
| Forged requests | CSRF token on every state-changing request, plus a check that the `Origin` is ours (also on sign-in). | `api/tests/test_login.py`, `test_security.py` |
| Access control | Every route needs a role; a test fails if any route is open by accident. Administrator-only routes are tested against every role. Roles are read from the database on each request, so changes apply at once. | `api/tests/test_authz.py`, `test_admin_users.py` |
| People | Administrator creates and disables accounts; disabling takes effect on the very next request; the last Administrator cannot be locked out; consultants need a recorded agreement date (enforced by the database too). | `api/tests/test_admin_users.py` |

## Not built yet (and where it comes)

**Two-step sign-in for Staff and Administrator and the 20-minute idle timeout (C02).** Until C02 is done, those roles sign in with a password only, which the BRD does not allow for real use. **Do not load real client data before C02.** Also still to come: the tamper-evident audit log (C03-C04; until then security events go to the redacted application log); consultant sign-in by emailed link and code (C21, so consultants cannot sign in yet); secure client links (C14); private file storage and virus scanning (C16); owner and assignment checks on real records (C07, C21); PAN and bank masking, encryption review and independent security test (C30).

## Production checklist (before any real data)

- TLS in front, using `deploy/nginx/`; set `LL_ENV=production`, exact `LL_ALLOWED_HOSTS` and https `LL_ALLOWED_ORIGINS`.
- Run uvicorn with `--no-server-header --proxy-headers --forwarded-allow-ips 127.0.0.1`, bound to 127.0.0.1 behind the proxy, so the address limit sees real client addresses.
- Create the first Administrator with `scripts/create-admin`; there is no web sign-up.
- Separate database accounts for migrations and for the running app (set up in C03 so the audit log can be made append-only).
- Hosting in India as required by the BRD (open question Q6).
