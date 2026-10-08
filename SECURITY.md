# Security

## Reporting a problem

Do not open a public issue. Email the project owner directly with what you found and how to reproduce it.

## What is in place now (chunk C00)

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

## Not built yet (and where it comes)

Sign-in, two-step verification and sessions (C01-C02); the audit log (C03-C04); role and ownership checks on every route (C01, tested in C02); private file storage and virus scanning (C16); secure client links (C14); rate limiting on sign-in (C01); PAN and bank masking, encryption review and independent security test (C30). Until those chunks land, the application has no user data and no sign-in.

## Production checklist (before any real data)

- TLS in front, using `deploy/nginx/`; set `LL_ENV=production`, exact `LL_ALLOWED_HOSTS` and https `LL_ALLOWED_ORIGINS`.
- Run uvicorn with `--no-server-header`, bound to 127.0.0.1 behind the proxy.
- Separate database accounts for migrations and for the running app (set up in C03 so the audit log can be made append-only).
- Hosting in India as required by the BRD (open question Q6).
