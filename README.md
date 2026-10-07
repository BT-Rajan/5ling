# Ledgerline (5ling)

Compliance and filing management for chartered accountant firms. Python (FastAPI) API and workers, React web client.

Status: planning. No application code yet.

## Where to look

| File | What it is |
| --- | --- |
| `docs/Ledgerline-BRD.docx` | Business Requirements Document, draft v1.0 |
| `docs/CHANGE_REQUESTS.md` | Client ID, task tickets, manual state-change emails |
| `docs/DEVELOPMENT_PLAN.md` | 38 small chunks, 146 tickets, in build order |
| `docs/tickets.csv` | The same tickets, ready to import as GitHub issues |
| `tools/gen_plan.py` | Generates the plan and CSV. Edit this, not the outputs. |

## Working rules

- One chunk at a time, in order; each ends with its "Done when" test passing.
- Branch per chunk (`c07-clients-create`), pull request references the LL- ticket IDs.
- Every request is authorised on the server. Every sensitive action writes an audit entry.
