# Ledgerline (5ling)

Compliance and filing management for chartered accountant firms. Python (FastAPI) API on MySQL 8, React web client that installs on Android as an app (PWA) and looks native there: Material Design 3, bottom navigation on phones, navigation rail on desktop, light and dark.

Status: chunk C00 (foundation) done. See `docs/DEVELOPMENT_PLAN.md`.

## Run it (Debian or Ubuntu, no Docker)

```bash
scripts/dev-setup     # once: installs MySQL, Redis, ClamAV, Mailpit; creates databases and a private .env
scripts/dev-up        # every day: starts everything on 127.0.0.1, applies migrations
```

Web http://localhost:5173 · API docs http://localhost:8000/api/docs · Mail http://localhost:8025

Needs Node 20+ and Python 3.12. `SKIP_CLAMAV=1 scripts/dev-setup` skips the virus scanner until chunk C16.

## Check it

```bash
cd api && . .venv/bin/activate
ruff check . && ruff format --check . && mypy && bandit -q -c pyproject.toml -r app migrations
pytest                                   # uses LL_TEST_DATABASE_URL from .env (export it first)
cd ../web && npm run lint && npm run typecheck && npm test && npm run build
```

## Where to look

| Path | What it is |
| --- | --- |
| `docs/Ledgerline-BRD.docx` | Business Requirements Document, draft v1.0 |
| `docs/CHANGE_REQUESTS.md` | Client ID, task tickets, manual state-change emails |
| `docs/DEVELOPMENT_PLAN.md` | 38 chunks, 149 tickets, in build order |
| `docs/tickets.csv` | The same tickets, ready to import as GitHub issues |
| `tools/gen_plan.py` | Generates the plan and CSV. Edit this, not the outputs. |
| `api/` | FastAPI app, migrations (Alembic), tests |
| `web/` | React app (Vite, TypeScript, MUI), service worker, manifest |
| `deploy/nginx/` | Production web server example with security headers |
| `SECURITY.md` | What is protected now, what is not built yet, production checklist |

## Working rules

- One chunk at a time, in order; each ends with its "Done when" test passing.
- Branch per chunk (`c07-clients-create`), pull request references the LL- ticket IDs.
- Every request is authorised on the server. Every sensitive action writes an audit entry.
- Never commit `.env`. Never log identifiers (PAN, GSTIN, email, account numbers): the logger redacts them, but do not rely on that.
