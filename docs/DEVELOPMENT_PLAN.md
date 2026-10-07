# Ledgerline - Development Plan

Source: `docs/Ledgerline-BRD.docx` (Draft v1.0, 8 Oct 2026) plus three change requests in `docs/CHANGE_REQUESTS.md`.

This plan breaks R1 (all Must requirements) and R2 (Should requirements) into small chunks. R3 (Could) is left for the Partners to choose at the R2 review.

## How the chunks work

- A chunk is a slice that can be built, tested and merged on its own, usually in one to three pull requests.
- Each chunk lists tickets, a "Done when" test, and what it depends on. Do not start a chunk until its dependencies are merged.
- Every ticket has an ID (`LL-001` and so on) and is also listed in `docs/tickets.csv`, ready to import as GitHub issues or into a tracker.
- Sizes: **S** is up to half a day, **M** is up to a day and a half. Types: DB, API, WEB, JOB (background), TEST, OPS.
- Security rules (server-side checks, audit entries) are part of each ticket, not a later phase. C30 and C31 prove them.

## Proposed stack (confirm before C00)

Python 3.12 with FastAPI and SQLAlchemy; PostgreSQL; Celery with Redis for jobs; React with Vite and TypeScript; S3-compatible object storage in an India region (MinIO in development); ClamAV for virus scanning; Mailpit for local email. This follows the BRD's Python and React direction.

## IDs used in the product (not to be confused with LL- dev tickets)

| What | Format | Rule |
| --- | --- | --- |
| Client ID | 6 digits, e.g. `100001` | Assigned automatically on save from a database sequence starting at 100001. Unique, never changes, never reused, even for closed clients. |
| Task ticket ID | `TKT-000001` | Every filing cycle and every ad hoc task gets one automatically. |
| Dev ticket ID | `LL-001` | Tickets in this plan. |

## Client emails on state changes

Every cycle stage change and every client status change creates a **draft** email. Nothing is sent automatically. Staff open the ticket, preview the draft, and click **Send update to client**. Sending is once-only, goes to the primary contact, and is audited. Scheduled alerts and reminders (first alert, chasing) stay automatic as in the BRD. Details in `docs/CHANGE_REQUESTS.md`.

## Chunk summary

| Chunk | Release | Title | Tickets | Depends on |
| --- | --- | --- | --- | --- |
| C00 | R1 | Foundation | 5 | - |
| C01 | R1 | Users and roles | 5 | C00 |
| C02 | R1 | Two-step sign-in and sessions | 4 | C01 |
| C03 | R1 | Audit log core | 3 | C01 |
| C04 | R1 | Audit verification and viewer | 4 | C03 |
| C05 | R1 | Packages and filing calendars | 4 | C01, C03 |
| C06 | R1 | Due-date engine | 5 | C05 |
| C07 | R1 | Clients: create | 5 | C05, C03 |
| C08 | R1 | Clients: change, search, reassign | 4 | C07 |
| C09 | R1 | Cycles and tickets | 6 | C07, C06 |
| C10 | R1 | Cycle generator | 4 | C09, C06 |
| C11 | R1 | Ad hoc task tickets | 2 | C09 |
| C12 | R1 | Bulk due-date extension | 3 | C10 |
| C13 | R1 | Email service and outbox | 4 | C00, C03 |
| C14 | R1 | Secure links and client portal shell | 3 | C13, C09 |
| C15 | R1 | Manual state-change emails | 6 | C13, C09, C07 |
| C16 | R1 | Documents and storage | 5 | C00, C03 |
| C17 | R1 | Checklist and client upload | 3 | C14, C16, C05 |
| C18 | R1 | Scheduled alerts and reminders | 4 | C17, C13, C10 |
| C19 | R1 | Internal alerts and escalation | 5 | C17, C09 |
| C20 | R1 | Daily work list | 3 | C18, C19 |
| C21 | R1 | Assignment and consultant access | 4 | C16, C09, C02 |
| C22 | R1 | Review: maker-checker | 4 | C21 |
| C23 | R1 | Filing and closure | 3 | C22 |
| C24 | R1 | Fees: plans and invoices | 5 | C07, C05 |
| C25 | R1 | Fees: payments ledger | 5 | C24 |
| C26 | R1 | Fees: approvals and overdue policy | 4 | C25, C23 |
| C27 | R1 | Dashboards | 3 | C20, C25 |
| C28 | R1 | Reports and exports | 5 | C27, C04 |
| C29 | R1 | Admin controls | 3 | C08, C06 |
| C30 | R1 | Security hardening | 4 | C28 |
| C31 | R1 | Resilience and performance | 4 | C28 |
| C32 | R1 | Migration, pilot and go-live | 4 | C30, C31 |
| C33 | R2 | Client import | 2 | C08 |
| C34 | R2 | Nil filings and phone follow-up | 2 | C23 |
| C35 | R2 | Client messages and completion | 3 | C23, C15 |
| C36 | R2 | WhatsApp or SMS | 2 | C18 |
| C37 | R2 | Package exceptions and access review | 2 | C08, C29 |

Total: 146 tickets.

## Chunks

### C00 - Foundation (R1)

**Goal:** A running skeleton that every later chunk builds on.  
**Depends on:** -

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-001 | OPS | S | Monorepo skeleton: api (FastAPI), web (React, Vite, TypeScript), docs | - |
| LL-002 | OPS | S | Docker Compose: Postgres, Redis, MinIO, ClamAV, Mailpit | INT-03, INT-04 |
| LL-003 | OPS | S | CI on every pull request: lint, type-check, unit tests | - |
| LL-004 | API | S | Config from environment; logger that never writes personal identifiers | NFR-SE-06 |
| LL-005 | DB | S | Alembic baseline; UTC storage and India-time display helpers | NFR-AU-01 |

**Done when:** `docker compose up` starts all services; CI is green on a trivial API and web test.

### C01 - Users and roles (R1)

**Goal:** Staff, Administrator and Consultant accounts exist and every route is guarded on the server.  
**Depends on:** C00

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-006 | DB | S | Users and roles tables | FR-AC-01 |
| LL-007 | API | M | Password sign-in with argon2 and session cookies | NFR-SE-02 |
| LL-008 | API | M | Central authorization dependency: role plus assignment check on every route | NFR-SE-01 |
| LL-009 | API | M | Administrator creates and disables staff and consultants; consultant agreement date recorded | FR-AC-02, BRD s12 |
| LL-010 | WEB | M | Sign-in page and app shell with role-aware navigation | - |

**Done when:** A staff user is refused on an admin route; a disabled user fails on the next request.

### C02 - Two-step sign-in and sessions (R1)

**Goal:** No staff or admin session without a second factor; sessions end when they should.  
**Depends on:** C01

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-011 | API | M | TOTP enrolment and verification for Staff and Administrator | NFR-SE-02 |
| LL-012 | API | S | 20-minute idle timeout; sessions end on disable or password change | NFR-SE-03, FR-AC-02 |
| LL-013 | WEB | S | Enrolment and code-entry screens | - |
| LL-014 | TEST | M | Authorization test harness: every role against every endpoint | NFR-SE-01, FR-AC-01 |

**Done when:** Role-by-endpoint test matrix runs in CI and every forbidden action is refused.

### C03 - Audit log core (R1)

**Goal:** Append-only, hash-chained audit trail that all later chunks write to.  
**Depends on:** C01

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-015 | DB | M | audit_entries table; database blocks update and delete | FR-AU-02 |
| LL-016 | API | M | Hash-chain writer (previous hash plus entry hash) | FR-AU-03 |
| LL-017 | API | M | audit() helper and middleware: sign-in, failures, denied requests | FR-AU-01 |

**Done when:** A scripted set of actions produces the expected entries with no gaps.

### C04 - Audit verification and viewer (R1)

**Goal:** Tampering is detectable and auditors can read and export the log.  
**Depends on:** C03

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-018 | API | S | Chain verification service and on-demand endpoint | FR-AU-03 |
| LL-019 | JOB | S | Daily verification job; alert Administrator and Partners on failure | FR-AU-03 |
| LL-020 | WEB | M | Admin audit viewer with filters and export including chain result | FR-AU-04 |
| LL-021 | TEST | S | Tamper test on a database copy | FR-AU-03 |

**Done when:** Altering a row in a test copy fails the check and raises an alert.

### C05 - Packages and filing calendars (R1)

**Goal:** Administrator can define what a client receives, with versioning.  
**Depends on:** C01, C03

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-022 | DB | M | Tables: filings, packages, package filings, checklist templates, calendars (versioned) | FR-PK-01, FR-PK-02, FR-PK-05 |
| LL-023 | API | M | Package API with publish rules | FR-PK-01 |
| LL-024 | API | M | Calendar API: type, frequency, due-date rule, FY handling, lead time | FR-PK-02 |
| LL-025 | WEB | M | Admin screens for packages and calendars | - |

**Done when:** A package cannot be published without a filing and a fee; editing creates a new version.

### C06 - Due-date engine (R1)

**Goal:** Correct due dates and alert dates from data, not code.  
**Depends on:** C05

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-026 | API | S | Holiday table with Administrator editing | FR-PK-04 |
| LL-027 | API | M | Due-date rule evaluator for monthly, quarterly, yearly and FY handling | FR-PK-02, NFR-MN-01 |
| LL-028 | API | M | Alert date: default 4 and 21 days, quarterly set by Admin, per-entry override, holiday rule | FR-PK-03, FR-PK-04 |
| LL-029 | WEB | S | Full-year due-date preview before saving a calendar | FR-PK-02 |
| LL-030 | TEST | S | Month-end and holiday test cases | NFR-AU-01 |

**Done when:** A test calendar with a known holiday gives the expected alert date.

### C07 - Clients: create (R1)

**Goal:** Clients exist with a unique 6-digit client ID, validated and de-duplicated.  
**Depends on:** C05, C03

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-031 | DB | S | clients and contacts tables; client_id from a database sequence starting at 100001 (6 digits, unique, immutable, never reused) | CR-01 |
| LL-032 | API | S | PAN and GSTIN validators | FR-CL-01 |
| LL-033 | API | M | Create-client API: required fields, duplicate block pointing to existing record | FR-CL-01, FR-CL-02, CR-01 |
| LL-034 | API | S | Several contacts with one primary; channel consent record (email on, WhatsApp and SMS off) with change log | FR-CL-03, FR-AL-08 |
| LL-035 | WEB | M | Create-client form showing the assigned client ID on save | CR-01 |

**Done when:** Duplicate PAN or GSTIN is blocked and links to the existing client; every client has a 6-digit ID.

### C08 - Clients: change, search, reassign (R1)

**Goal:** Clients can be maintained safely and found by the right people.  
**Depends on:** C07

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-036 | API | M | Change owner, package or status with reason; history shown | FR-CL-04 |
| LL-037 | API | M | Search and filter by name, PAN, owner, package, status; staff see own clients only; client ID searchable | FR-CL-06, CR-01 |
| LL-038 | API | S | Reassign moves access immediately | FR-AC-03 |
| LL-039 | WEB | M | Client list and client detail with history | - |

**Done when:** Previous owner sees 'no access' on the next request after reassignment.

### C09 - Cycles and tickets (R1)

**Goal:** Every task is a ticket with an ID; stages follow the BRD and illegal moves are refused.  
**Depends on:** C07, C06

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-040 | DB | M | tickets table: TKT-000001 style ID from a sequence; type FILING or TASK; linked to client | CR-02 |
| LL-041 | DB | M | cycles table linked one-to-one to FILING tickets; package version recorded | FR-PK-05, FR-CY-02 |
| LL-042 | API | M | Stage list and legal-transition table | FR-CY-02 |
| LL-043 | API | M | Transition endpoint: server refuses illegal moves; writes history and audit | FR-CY-02, FR-AU-01 |
| LL-044 | API | S | Emit state_changed event on every transition (consumed by C15) | CR-03 |
| LL-045 | WEB | M | Ticket detail page with stage timeline | CR-02 |

**Done when:** No stage can be skipped; every cycle has a TKT ID; each stage change emits a state_changed event.

### C10 - Cycle generator (R1)

**Goal:** Cycles and their tickets appear automatically and never twice.  
**Depends on:** C09, C06

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-046 | JOB | M | Generate current-FY cycles with tickets when a package is assigned | FR-CY-01, CR-02 |
| LL-047 | JOB | M | Rolling top-up job keeping 12 months ahead; unique key client, filing, period | FR-CY-01, FR-CY-04 |
| LL-048 | JOB | S | Failure raises Administrator alert and log entry; safe re-run | FR-CY-05 |
| LL-049 | TEST | S | Tests: double run, forced failure, closed client creates nothing | FR-CY-04, FR-CY-05, FR-CL-04 |

**Done when:** Running the job twice creates zero duplicates; a forced failure alerts Administrator.

### C11 - Ad hoc task tickets (R1)

**Goal:** Work that is not a filing (call a client, collect a signature) is also a ticket.  
**Depends on:** C09

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-050 | API | M | Create and update TASK tickets linked to a client | CR-02 |
| LL-051 | WEB | M | Ticket list and filters (type, stage, owner, client ID, TKT ID) | CR-02 |

**Done when:** A manual task gets a TKT ID, an assignee and a due date, and appears in lists.

### C12 - Bulk due-date extension (R1)

**Goal:** One action applies a government extension to all affected open cycles.  
**Depends on:** C10

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-052 | API | M | Apply new due date to all open cycles of a filing with reason | FR-PK-06 |
| LL-053 | API | S | Recompute alert dates; audit entry | FR-PK-06 |
| LL-054 | WEB | M | Admin screen with preview of affected cycles | FR-PK-06 |

**Done when:** Affected cycles show the new date, alerts are recomputed, change is logged.

### C13 - Email service and outbox (R1)

**Goal:** One reliable way to send email with delivery status.  
**Depends on:** C00, C03

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-055 | API | M | Email provider interface (SMTP or cloud) with Mailpit in development | INT-01 |
| LL-056 | API | M | Template engine; variables include client name, client ID, ticket ID, stage, due date | CR-03 |
| LL-057 | DB | M | Outbox table with statuses: draft, queued, sent, delivered, failed, bounced | FR-AL-06, CR-03 |
| LL-058 | JOB | M | Send worker with 24-hour retry and provider delivery callbacks | INT-01 |

**Done when:** A test email moves queued to sent to delivered; a forced failure shows failed.

### C14 - Secure links and client portal shell (R1)

**Goal:** Clients act on one cycle through a private, expiring link.  
**Depends on:** C13, C09

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-059 | API | M | Link tokens: one client, one cycle, hashed at rest, 72-hour default expiry | FR-AL-02 |
| LL-060 | API | M | Staff renew link; client can request a new link | FR-AL-02 |
| LL-061 | WEB | M | Mobile-first client portal shell validating the link | FR-AL-02, NFR-US-01 |

**Done when:** An expired or reused link shows a clear message and offers a new link; nothing is visible without a valid link.

### C15 - Manual state-change emails (R1)

**Goal:** Every state change prepares an email to the client; it is sent only when staff click Send.  
**Depends on:** C13, C09, C07

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-062 | API | M | Consume state_changed: create one DRAFT notification per stage change (nothing is sent) | CR-03 |
| LL-063 | API | M | Draft preview, optional personal note, Send endpoint with once-only guard; Own or Admin only | CR-03, FR-AC-01 |
| LL-064 | WEB | M | 'Send update to client' button and draft badge on ticket page | CR-03 |
| LL-065 | API | S | Client status changes (active, paused, closed) also create drafts | CR-03, FR-CL-04 |
| LL-066 | API | S | Send goes to primary contact; other contacts copied only if chosen; send result audited | FR-CL-03, FR-AU-01 |
| LL-067 | TEST | S | Tests: no auto-send, one draft per change, double-click sends once, audit entries present | CR-03 |

**Done when:** No email leaves the system without a click; each state change yields exactly one draft.

### C16 - Documents and storage (R1)

**Goal:** Files are stored safely, never overwritten and scanned.  
**Depends on:** C00, C03

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-068 | API | M | Encrypted object-storage adapter (India region); no public addresses | INT-04, NFR-SE-05, NFR-SE-04 |
| LL-069 | API | M | documents and versions tables; upload never overwrites | FR-AL-05 |
| LL-070 | JOB | M | Virus scan on upload; file held until clean; type and size limits | FR-AL-04, INT-03 |
| LL-071 | API | M | Short-lived authorised download links; every view and download audited | NFR-SE-05, FR-AU-01 |
| LL-072 | WEB | S | Version history screen for staff | FR-AL-05 |

**Done when:** A new upload becomes version 2; an infected file is rejected and logged; no public file URL exists.

### C17 - Checklist and client upload (R1)

**Goal:** Clients upload against checklist items and see what is pending.  
**Depends on:** C14, C16, C05

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-073 | API | S | Create cycle checklist from the package version | FR-AL-04, FR-PK-05 |
| LL-074 | WEB | M | Portal upload per checklist item with pending list | FR-AL-04 |
| LL-075 | API | S | Rejection reasons shown to client and logged | FR-AL-04 |

**Done when:** Rejected files show a reason to the client and appear in the log.

### C18 - Scheduled alerts and reminders (R1)

**Goal:** Clients are alerted automatically before the due date and chased until complete.  
**Depends on:** C17, C13, C10

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-076 | JOB | M | First alert at alert date with secure link, due date and checklist (automatic) | FR-AL-01 |
| LL-077 | JOB | M | Reminders every 2 days, then daily after due date; stop when checklist complete | FR-AL-03 |
| LL-078 | API | S | Failed or bounced send appears on the owner's work list within 15 minutes | FR-AL-06 |
| LL-079 | TEST | S | Tests: single send, stop condition, failure surfacing | FR-AL-01, FR-AL-03, FR-AL-06 |

**Done when:** Alert sent once on the computed date; reminders stop within one hour of a complete checklist.

### C19 - Internal alerts and escalation (R1)

**Goal:** The right person knows at once when documents arrive or are missing.  
**Depends on:** C17, C09

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-080 | API | M | In-app notifications table and bell | FR-IN-01 |
| LL-081 | API | M | Last required item arrives: stage Documents received and owner notified within 1 minute | FR-IN-01, FR-CY-02 |
| LL-082 | JOB | M | Missing after first alert plus grace: 'Waiting on client' with exact missing items | FR-IN-02 |
| LL-083 | JOB | M | Escalate to Administrator 2 days before due date; one-time; dismiss needs a note | FR-IN-03 |
| LL-084 | WEB | S | Notification list and dismiss-with-note screens | FR-IN-01, FR-IN-03 |

**Done when:** Escalation is created once and cannot be dismissed without a note.

### C20 - Daily work list (R1)

**Goal:** One landing page for the coordinator.  
**Depends on:** C18, C19

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-085 | API | M | Work-list API: received, missing, due this week, overdue | FR-IN-04 |
| LL-086 | WEB | M | Work-list screen as the staff landing page | FR-IN-04 |
| LL-087 | TEST | S | Count-consistency test | FR-IN-04 |

**Done when:** Work-list counts equal report counts for the same owner and date.

### C21 - Assignment and consultant access (R1)

**Goal:** Work goes to the right person with limited access that ends on time.  
**Depends on:** C16, C09, C02

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-088 | API | M | assignments table; assign in-house or consultant with work due date and note; assignee notified | FR-WF-01 |
| LL-089 | API | M | Consultant sign-in: emailed link plus one-time code | NFR-SE-02 |
| LL-090 | WEB | M | Task-scoped consultant view: assigned cycle documents and upload space only | FR-WF-02 |
| LL-091 | API | S | Withdraw assignment; deny and log everything else | FR-WF-02, FR-AU-01 |

**Done when:** After closure the consultant link says 'no access'; any other client record is denied and logged.

### C22 - Review: maker-checker (R1)

**Goal:** The preparer cannot approve their own work.  
**Depends on:** C21

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-092 | API | M | Preparer uploads work as a new version; stage moves to In review | BRD s6, FR-AL-05 |
| LL-093 | API | M | Approve or return with comments; return sets stage to Preparing | FR-WF-04 |
| LL-094 | API | S | Server refuses approval by the preparer | FR-WF-03 |
| LL-095 | WEB | M | Review screen with comments | FR-WF-04 |

**Done when:** Approve is unavailable to the preparer and refused by the server if attempted.

### C23 - Filing and closure (R1)

**Goal:** A filing is complete only with proof; closure ends outside access.  
**Depends on:** C22

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-096 | API | M | Filed requires uploaded acknowledgement and reference number | FR-WF-05 |
| LL-097 | API | S | Close ticket: consultant access ends automatically; next cycle already scheduled | FR-WF-02, BRD s6 |
| LL-098 | TEST | M | End-to-end lifecycle test: Scheduled to Closed, with a draft at every state change | FR-CY-02, CR-03 |

**Done when:** Filed is blocked without proof and reference; full lifecycle test passes.

### C24 - Fees: plans and invoices (R1)

**Goal:** Fee plans, gapless invoices and tax.  
**Depends on:** C07, C05

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-099 | API | M | Fee plan from package; client-specific override with Administrator approval | FR-FE-01 |
| LL-100 | API | M | Invoice series with gapless numbers | FR-FE-02 |
| LL-101 | API | S | Configurable tax rates (GST) | FR-FE-09 |
| LL-102 | JOB | M | Invoice scheduler for monthly, quarterly, yearly, one-time | FR-FE-02 |
| LL-103 | WEB | M | Fee plan and invoice screens | - |

**Done when:** No invoice number is skipped or reused; invoice totals match the test set.

### C25 - Fees: payments ledger (R1)

**Goal:** Append-only ledger and live fee status.  
**Depends on:** C24

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-104 | API | M | Append-only ledger; payments including part payments | FR-FE-03 |
| LL-105 | API | S | Reversing entries; originals stay visible | FR-FE-03 |
| LL-106 | API | M | Fee status Paid, Due, Overdue, Part paid on client list and ticket | FR-FE-04 |
| LL-107 | API | S | Fees visible only for own clients; Administrator sees all | FR-FE-05 |
| LL-108 | WEB | M | Payment entry and ledger screens | FR-FE-03 |

**Done when:** Balance after a reversal equals the expected figure; status updates the moment a payment is recorded.

### C26 - Fees: approvals and overdue policy (R1)

**Goal:** Control fee leakage and filings for non-payers.  
**Depends on:** C25, C23

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-109 | API | M | Credit note, discount, write-off stay pending until Administrator approves | FR-FE-08 |
| LL-110 | API | M | Overdue policy setting: reminder only, warn, or block Filed (default set after Q3) | FR-FE-06 |
| LL-111 | API | S | One-time Administrator override with reason | FR-FE-06 |
| LL-112 | WEB | M | Approval and policy screens | FR-FE-06, FR-FE-08 |

**Done when:** With the block policy on, Filed is refused for an overdue client unless an override exists.

### C27 - Dashboards (R1)

**Goal:** See the whole portfolio and drill into the list behind every number.  
**Depends on:** C20, C25

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-113 | API | M | Administrator dashboard API and screen | FR-RP-01 |
| LL-114 | WEB | M | Staff dashboard scoped to own clients | FR-RP-02 |
| LL-115 | TEST | S | Tests: figure equals list; no cross-owner counts | FR-RP-01, FR-RP-02 |

**Done when:** Each figure's list size equals the figure; staff never see counts that include other owners.

### C28 - Reports and exports (R1)

**Goal:** Reports for partners and auditors, with access rules and logging.  
**Depends on:** C27, C04

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-116 | API | M | Filing status by period; late filings with reasons | FR-RP-03 |
| LL-117 | API | M | Fee aging; consultant turnaround; alert delivery | FR-RP-03 |
| LL-118 | API | M | Export to spreadsheet and PDF under the same access rules | FR-RP-04 |
| LL-119 | API | S | Every export written to the audit log | FR-RP-04, FR-AU-01 |
| LL-120 | WEB | M | Reports screens | FR-RP-03 |

**Done when:** A staff export contains only permitted rows and writes a log entry.

### C29 - Admin controls (R1)

**Goal:** Rules are editable data and emergency access is controlled.  
**Depends on:** C08, C06

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-121 | API | M | Break-glass access with mandatory reason, flagged in audit | FR-AC-05 |
| LL-122 | API | M | Admin settings: grace period, reminder interval, escalation point, link expiry, idle timeout | NFR-MN-01, FR-AL-02, FR-IN-03 |
| LL-123 | WEB | M | Settings and break-glass screens | - |

**Done when:** Opening a client you do not own asks for a reason and logs it; a due-date change needs no release.

### C30 - Security hardening (R1)

**Goal:** Close the gaps before the independent test.  
**Depends on:** C28

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-124 | API | M | Mask PAN and bank details on screen; log review | NFR-SE-06 |
| LL-125 | OPS | M | Encryption at rest and key separation review | NFR-SE-04 |
| LL-126 | TEST | M | Full authorization matrix against section 8 of the BRD | FR-AC-01, NFR-SE-01 |
| LL-127 | OPS | M | Independent security test; fix critical and high findings | NFR-SE-07 |

**Done when:** Log review finds no PAN or bank detail; no unauthenticated file access.

### C31 - Resilience and performance (R1)

**Goal:** Backups, monitoring and speed targets proven.  
**Depends on:** C28

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-128 | OPS | M | Daily backups to a separate location; restore test script | NFR-AV-02 |
| LL-129 | OPS | M | Monitoring and alerts for failed jobs, failed emails, error spikes | NFR-OB-01 |
| LL-130 | TEST | M | Load test: 5,000 clients, 50,000 cycles | NFR-PF-01, NFR-SC-01 |
| LL-131 | TEST | M | Accessibility (WCAG 2.1 AA) and cross-browser pass | NFR-US-01 |

**Done when:** Restore test recorded; load test meets targets; failed job alerts within 5 minutes.

### C32 - Migration, pilot and go-live (R1)

**Goal:** Real data in, 20-client pilot, sign-off.  
**Depends on:** C30, C31

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-132 | OPS | M | Migration scripts per Q5 with count and 5% sample check | BRD s15, Q5 |
| LL-133 | OPS | S | Load Q4 filing types and due-date rules | Q4 |
| LL-134 | OPS | M | Pilot with 20 clients; defect triage | BRD s14 |
| LL-135 | OPS | S | Sign-off pack with test evidence | BRD s15 |

**Done when:** All M acceptance conditions pass; 2 weeks of pilot with no severity 1 defect; written sign-off.

### C33 - Client import (R2)

**Goal:** Bulk load clients safely.  
**Depends on:** C08

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-136 | API | M | Spreadsheet upload and validation report | FR-CL-05 |
| LL-137 | WEB | M | Confirm-and-save step; import screen | FR-CL-05 |

**Done when:** Rows with errors are listed with reasons; valid rows save only after confirmation.

### C34 - Nil filings and phone follow-up (R2)

**Goal:** Handle no-activity filings and call notes.  
**Depends on:** C23

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-138 | API | M | Client confirms no activity; cycle skips to review | FR-CY-03 |
| LL-139 | API | M | Phone-contact note with promised date and reminder | FR-IN-05 |

**Done when:** Client confirmation is shown to the reviewer; a promised date raises a reminder.

### C35 - Client messages and completion (R2)

**Goal:** Two-way messages and a way to deliver the acknowledgement.  
**Depends on:** C23, C15

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-140 | API | M | Portal reply stored against the cycle; owner notified | FR-AL-07 |
| LL-141 | API | M | Completion draft with acknowledgement download link (manual send) | FR-WF-06, CR-03 |
| LL-142 | JOB | M | Fee reminders at set intervals after due date; stop when paid | FR-FE-07 |

**Done when:** Client message notifies the owner; completion email is a manual draft per CR-03.

### C36 - WhatsApp or SMS (R2)

**Goal:** Second channel with opt-out respected.  
**Depends on:** C18

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-143 | API | M | WhatsApp or SMS provider adapter and approved templates | INT-02, C-2, Q2 |
| LL-144 | API | M | Channel selection honours consent record; fallback to email | FR-AL-08, INT-02 |

**Done when:** An opted-out channel is never used; failure falls back to email.

### C37 - Package exceptions and access review (R2)

**Goal:** Per-client filing changes with approval; quarterly certification.  
**Depends on:** C08, C29

| Ticket | Type | Size | Task | BRD / CR |
| --- | --- | --- | --- | --- |
| LL-145 | API | M | Add or remove filings for one client; removals need Administrator approval | FR-PK-07 |
| LL-146 | API | M | Access review export for quarterly certification | FR-AC-04 |

**Done when:** A removal stays pending until approved; the access export shows the date generated.


## R3 (Could have)

Consultant fee tracking (FR-WF-07), single sign-on (INT-05) and accounting export (INT-06). Scope to be agreed at the R2 review.

## Open BRD questions that gate chunks

| Question | Gates |
| --- | --- |
| Q1 Retention periods | Design freeze, before C16 |
| Q3 Block or only warn on overdue fees | C26 default |
| Q4 Filing types and due-date rules | C32 data load |
| Q5 Migration volume and source | C32 |
| Q6 India-only hosting | Design freeze, before C16 |
| Q2 WhatsApp or SMS provider | C36 |
