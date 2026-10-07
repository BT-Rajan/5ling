"""Single source of truth for the Ledgerline development plan.

Run:  python tools/gen_plan.py
Writes: docs/DEVELOPMENT_PLAN.md and docs/tickets.csv

Dev ticket IDs (LL-001, LL-002 ...) are assigned in order. Once work starts, only
append new tickets at the end of the list so existing IDs do not shift.
Sizes: S = up to half a day, M = up to a day and a half.
Types: DB, API, WEB, JOB, TEST, OPS.
"""
import csv
import os

# chunk = (id, release, title, goal, depends_on, done_when, [tickets])
# ticket = (title, type, size, refs)
CHUNKS = [
    ("C00", "R1", "Foundation",
     "A running skeleton that every later chunk builds on.", "-",
     "`docker compose up` starts all services; CI is green on a trivial API and web test.",
     [("Monorepo skeleton: api (FastAPI), web (React, Vite, TypeScript), docs", "OPS", "S", ""),
      ("Docker Compose: Postgres, Redis, MinIO, ClamAV, Mailpit", "OPS", "S", "INT-03, INT-04"),
      ("CI on every pull request: lint, type-check, unit tests", "OPS", "S", ""),
      ("Config from environment; logger that never writes personal identifiers", "API", "S", "NFR-SE-06"),
      ("Alembic baseline; UTC storage and India-time display helpers", "DB", "S", "NFR-AU-01")]),

    ("C01", "R1", "Users and roles",
     "Staff, Administrator and Consultant accounts exist and every route is guarded on the server.", "C00",
     "A staff user is refused on an admin route; a disabled user fails on the next request.",
     [("Users and roles tables", "DB", "S", "FR-AC-01"),
      ("Password sign-in with argon2 and session cookies", "API", "M", "NFR-SE-02"),
      ("Central authorization dependency: role plus assignment check on every route", "API", "M", "NFR-SE-01"),
      ("Administrator creates and disables staff and consultants; consultant agreement date recorded", "API", "M", "FR-AC-02, BRD s12"),
      ("Sign-in page and app shell with role-aware navigation", "WEB", "M", "")]),

    ("C02", "R1", "Two-step sign-in and sessions",
     "No staff or admin session without a second factor; sessions end when they should.", "C01",
     "Role-by-endpoint test matrix runs in CI and every forbidden action is refused.",
     [("TOTP enrolment and verification for Staff and Administrator", "API", "M", "NFR-SE-02"),
      ("20-minute idle timeout; sessions end on disable or password change", "API", "S", "NFR-SE-03, FR-AC-02"),
      ("Enrolment and code-entry screens", "WEB", "S", ""),
      ("Authorization test harness: every role against every endpoint", "TEST", "M", "NFR-SE-01, FR-AC-01")]),

    ("C03", "R1", "Audit log core",
     "Append-only, hash-chained audit trail that all later chunks write to.", "C01",
     "A scripted set of actions produces the expected entries with no gaps.",
     [("audit_entries table; database blocks update and delete", "DB", "M", "FR-AU-02"),
      ("Hash-chain writer (previous hash plus entry hash)", "API", "M", "FR-AU-03"),
      ("audit() helper and middleware: sign-in, failures, denied requests", "API", "M", "FR-AU-01")]),

    ("C04", "R1", "Audit verification and viewer",
     "Tampering is detectable and auditors can read and export the log.", "C03",
     "Altering a row in a test copy fails the check and raises an alert.",
     [("Chain verification service and on-demand endpoint", "API", "S", "FR-AU-03"),
      ("Daily verification job; alert Administrator and Partners on failure", "JOB", "S", "FR-AU-03"),
      ("Admin audit viewer with filters and export including chain result", "WEB", "M", "FR-AU-04"),
      ("Tamper test on a database copy", "TEST", "S", "FR-AU-03")]),

    ("C05", "R1", "Packages and filing calendars",
     "Administrator can define what a client receives, with versioning.", "C01, C03",
     "A package cannot be published without a filing and a fee; editing creates a new version.",
     [("Tables: filings, packages, package filings, checklist templates, calendars (versioned)", "DB", "M", "FR-PK-01, FR-PK-02, FR-PK-05"),
      ("Package API with publish rules", "API", "M", "FR-PK-01"),
      ("Calendar API: type, frequency, due-date rule, FY handling, lead time", "API", "M", "FR-PK-02"),
      ("Admin screens for packages and calendars", "WEB", "M", "")]),

    ("C06", "R1", "Due-date engine",
     "Correct due dates and alert dates from data, not code.", "C05",
     "A test calendar with a known holiday gives the expected alert date.",
     [("Holiday table with Administrator editing", "API", "S", "FR-PK-04"),
      ("Due-date rule evaluator for monthly, quarterly, yearly and FY handling", "API", "M", "FR-PK-02, NFR-MN-01"),
      ("Alert date: default 4 and 21 days, quarterly set by Admin, per-entry override, holiday rule", "API", "M", "FR-PK-03, FR-PK-04"),
      ("Full-year due-date preview before saving a calendar", "WEB", "S", "FR-PK-02"),
      ("Month-end and holiday test cases", "TEST", "S", "NFR-AU-01")]),

    ("C07", "R1", "Clients: create",
     "Clients exist with a unique 6-digit client ID, validated and de-duplicated.", "C05, C03",
     "Duplicate PAN or GSTIN is blocked and links to the existing client; every client has a 6-digit ID.",
     [("clients and contacts tables; client_id from a database sequence starting at 100001 (6 digits, unique, immutable, never reused)", "DB", "S", "CR-01"),
      ("PAN and GSTIN validators", "API", "S", "FR-CL-01"),
      ("Create-client API: required fields, duplicate block pointing to existing record", "API", "M", "FR-CL-01, FR-CL-02, CR-01"),
      ("Several contacts with one primary; channel consent record (email on, WhatsApp and SMS off) with change log", "API", "S", "FR-CL-03, FR-AL-08"),
      ("Create-client form showing the assigned client ID on save", "WEB", "M", "CR-01")]),

    ("C08", "R1", "Clients: change, search, reassign",
     "Clients can be maintained safely and found by the right people.", "C07",
     "Previous owner sees 'no access' on the next request after reassignment.",
     [("Change owner, package or status with reason; history shown", "API", "M", "FR-CL-04"),
      ("Search and filter by name, PAN, owner, package, status; staff see own clients only; client ID searchable", "API", "M", "FR-CL-06, CR-01"),
      ("Reassign moves access immediately", "API", "S", "FR-AC-03"),
      ("Client list and client detail with history", "WEB", "M", "")]),

    ("C09", "R1", "Cycles and tickets",
     "Every task is a ticket with an ID; stages follow the BRD and illegal moves are refused.", "C07, C06",
     "No stage can be skipped; every cycle has a TKT ID; each stage change emits a state_changed event.",
     [("tickets table: TKT-000001 style ID from a sequence; type FILING or TASK; linked to client", "DB", "M", "CR-02"),
      ("cycles table linked one-to-one to FILING tickets; package version recorded", "DB", "M", "FR-PK-05, FR-CY-02"),
      ("Stage list and legal-transition table", "API", "M", "FR-CY-02"),
      ("Transition endpoint: server refuses illegal moves; writes history and audit", "API", "M", "FR-CY-02, FR-AU-01"),
      ("Emit state_changed event on every transition (consumed by C15)", "API", "S", "CR-03"),
      ("Ticket detail page with stage timeline", "WEB", "M", "CR-02")]),

    ("C10", "R1", "Cycle generator",
     "Cycles and their tickets appear automatically and never twice.", "C09, C06",
     "Running the job twice creates zero duplicates; a forced failure alerts Administrator.",
     [("Generate current-FY cycles with tickets when a package is assigned", "JOB", "M", "FR-CY-01, CR-02"),
      ("Rolling top-up job keeping 12 months ahead; unique key client, filing, period", "JOB", "M", "FR-CY-01, FR-CY-04"),
      ("Failure raises Administrator alert and log entry; safe re-run", "JOB", "S", "FR-CY-05"),
      ("Tests: double run, forced failure, closed client creates nothing", "TEST", "S", "FR-CY-04, FR-CY-05, FR-CL-04")]),

    ("C11", "R1", "Ad hoc task tickets",
     "Work that is not a filing (call a client, collect a signature) is also a ticket.", "C09",
     "A manual task gets a TKT ID, an assignee and a due date, and appears in lists.",
     [("Create and update TASK tickets linked to a client", "API", "M", "CR-02"),
      ("Ticket list and filters (type, stage, owner, client ID, TKT ID)", "WEB", "M", "CR-02")]),

    ("C12", "R1", "Bulk due-date extension",
     "One action applies a government extension to all affected open cycles.", "C10",
     "Affected cycles show the new date, alerts are recomputed, change is logged.",
     [("Apply new due date to all open cycles of a filing with reason", "API", "M", "FR-PK-06"),
      ("Recompute alert dates; audit entry", "API", "S", "FR-PK-06"),
      ("Admin screen with preview of affected cycles", "WEB", "M", "FR-PK-06")]),

    ("C13", "R1", "Email service and outbox",
     "One reliable way to send email with delivery status.", "C00, C03",
     "A test email moves queued to sent to delivered; a forced failure shows failed.",
     [("Email provider interface (SMTP or cloud) with Mailpit in development", "API", "M", "INT-01"),
      ("Template engine; variables include client name, client ID, ticket ID, stage, due date", "API", "M", "CR-03"),
      ("Outbox table with statuses: draft, queued, sent, delivered, failed, bounced", "DB", "M", "FR-AL-06, CR-03"),
      ("Send worker with 24-hour retry and provider delivery callbacks", "JOB", "M", "INT-01")]),

    ("C14", "R1", "Secure links and client portal shell",
     "Clients act on one cycle through a private, expiring link.", "C13, C09",
     "An expired or reused link shows a clear message and offers a new link; nothing is visible without a valid link.",
     [("Link tokens: one client, one cycle, hashed at rest, 72-hour default expiry", "API", "M", "FR-AL-02"),
      ("Staff renew link; client can request a new link", "API", "M", "FR-AL-02"),
      ("Mobile-first client portal shell validating the link", "WEB", "M", "FR-AL-02, NFR-US-01")]),

    ("C15", "R1", "Manual state-change emails",
     "Every state change prepares an email to the client; it is sent only when staff click Send.", "C13, C09, C07",
     "No email leaves the system without a click; each state change yields exactly one draft.",
     [("Consume state_changed: create one DRAFT notification per stage change (nothing is sent)", "API", "M", "CR-03"),
      ("Draft preview, optional personal note, Send endpoint with once-only guard; Own or Admin only", "API", "M", "CR-03, FR-AC-01"),
      ("'Send update to client' button and draft badge on ticket page", "WEB", "M", "CR-03"),
      ("Client status changes (active, paused, closed) also create drafts", "API", "S", "CR-03, FR-CL-04"),
      ("Send goes to primary contact; other contacts copied only if chosen; send result audited", "API", "S", "FR-CL-03, FR-AU-01"),
      ("Tests: no auto-send, one draft per change, double-click sends once, audit entries present", "TEST", "S", "CR-03")]),

    ("C16", "R1", "Documents and storage",
     "Files are stored safely, never overwritten and scanned.", "C00, C03",
     "A new upload becomes version 2; an infected file is rejected and logged; no public file URL exists.",
     [("Encrypted object-storage adapter (India region); no public addresses", "API", "M", "INT-04, NFR-SE-05, NFR-SE-04"),
      ("documents and versions tables; upload never overwrites", "API", "M", "FR-AL-05"),
      ("Virus scan on upload; file held until clean; type and size limits", "JOB", "M", "FR-AL-04, INT-03"),
      ("Short-lived authorised download links; every view and download audited", "API", "M", "NFR-SE-05, FR-AU-01"),
      ("Version history screen for staff", "WEB", "S", "FR-AL-05")]),

    ("C17", "R1", "Checklist and client upload",
     "Clients upload against checklist items and see what is pending.", "C14, C16, C05",
     "Rejected files show a reason to the client and appear in the log.",
     [("Create cycle checklist from the package version", "API", "S", "FR-AL-04, FR-PK-05"),
      ("Portal upload per checklist item with pending list", "WEB", "M", "FR-AL-04"),
      ("Rejection reasons shown to client and logged", "API", "S", "FR-AL-04")]),

    ("C18", "R1", "Scheduled alerts and reminders",
     "Clients are alerted automatically before the due date and chased until complete.", "C17, C13, C10",
     "Alert sent once on the computed date; reminders stop within one hour of a complete checklist.",
     [("First alert at alert date with secure link, due date and checklist (automatic)", "JOB", "M", "FR-AL-01"),
      ("Reminders every 2 days, then daily after due date; stop when checklist complete", "JOB", "M", "FR-AL-03"),
      ("Failed or bounced send appears on the owner's work list within 15 minutes", "API", "S", "FR-AL-06"),
      ("Tests: single send, stop condition, failure surfacing", "TEST", "S", "FR-AL-01, FR-AL-03, FR-AL-06")]),

    ("C19", "R1", "Internal alerts and escalation",
     "The right person knows at once when documents arrive or are missing.", "C17, C09",
     "Escalation is created once and cannot be dismissed without a note.",
     [("In-app notifications table and bell", "API", "M", "FR-IN-01"),
      ("Last required item arrives: stage Documents received and owner notified within 1 minute", "API", "M", "FR-IN-01, FR-CY-02"),
      ("Missing after first alert plus grace: 'Waiting on client' with exact missing items", "JOB", "M", "FR-IN-02"),
      ("Escalate to Administrator 2 days before due date; one-time; dismiss needs a note", "JOB", "M", "FR-IN-03"),
      ("Notification list and dismiss-with-note screens", "WEB", "S", "FR-IN-01, FR-IN-03")]),

    ("C20", "R1", "Daily work list",
     "One landing page for the coordinator.", "C18, C19",
     "Work-list counts equal report counts for the same owner and date.",
     [("Work-list API: received, missing, due this week, overdue", "API", "M", "FR-IN-04"),
      ("Work-list screen as the staff landing page", "WEB", "M", "FR-IN-04"),
      ("Count-consistency test", "TEST", "S", "FR-IN-04")]),

    ("C21", "R1", "Assignment and consultant access",
     "Work goes to the right person with limited access that ends on time.", "C16, C09, C02",
     "After closure the consultant link says 'no access'; any other client record is denied and logged.",
     [("assignments table; assign in-house or consultant with work due date and note; assignee notified", "API", "M", "FR-WF-01"),
      ("Consultant sign-in: emailed link plus one-time code", "API", "M", "NFR-SE-02"),
      ("Task-scoped consultant view: assigned cycle documents and upload space only", "WEB", "M", "FR-WF-02"),
      ("Withdraw assignment; deny and log everything else", "API", "S", "FR-WF-02, FR-AU-01")]),

    ("C22", "R1", "Review: maker-checker",
     "The preparer cannot approve their own work.", "C21",
     "Approve is unavailable to the preparer and refused by the server if attempted.",
     [("Preparer uploads work as a new version; stage moves to In review", "API", "M", "BRD s6, FR-AL-05"),
      ("Approve or return with comments; return sets stage to Preparing", "API", "M", "FR-WF-04"),
      ("Server refuses approval by the preparer", "API", "S", "FR-WF-03"),
      ("Review screen with comments", "WEB", "M", "FR-WF-04")]),

    ("C23", "R1", "Filing and closure",
     "A filing is complete only with proof; closure ends outside access.", "C22",
     "Filed is blocked without proof and reference; full lifecycle test passes.",
     [("Filed requires uploaded acknowledgement and reference number", "API", "M", "FR-WF-05"),
      ("Close ticket: consultant access ends automatically; next cycle already scheduled", "API", "S", "FR-WF-02, BRD s6"),
      ("End-to-end lifecycle test: Scheduled to Closed, with a draft at every state change", "TEST", "M", "FR-CY-02, CR-03")]),

    ("C24", "R1", "Fees: plans and invoices",
     "Fee plans, gapless invoices and tax.", "C07, C05",
     "No invoice number is skipped or reused; invoice totals match the test set.",
     [("Fee plan from package; client-specific override with Administrator approval", "API", "M", "FR-FE-01"),
      ("Invoice series with gapless numbers", "API", "M", "FR-FE-02"),
      ("Configurable tax rates (GST)", "API", "S", "FR-FE-09"),
      ("Invoice scheduler for monthly, quarterly, yearly, one-time", "JOB", "M", "FR-FE-02"),
      ("Fee plan and invoice screens", "WEB", "M", "")]),

    ("C25", "R1", "Fees: payments ledger",
     "Append-only ledger and live fee status.", "C24",
     "Balance after a reversal equals the expected figure; status updates the moment a payment is recorded.",
     [("Append-only ledger; payments including part payments", "API", "M", "FR-FE-03"),
      ("Reversing entries; originals stay visible", "API", "S", "FR-FE-03"),
      ("Fee status Paid, Due, Overdue, Part paid on client list and ticket", "API", "M", "FR-FE-04"),
      ("Fees visible only for own clients; Administrator sees all", "API", "S", "FR-FE-05"),
      ("Payment entry and ledger screens", "WEB", "M", "FR-FE-03")]),

    ("C26", "R1", "Fees: approvals and overdue policy",
     "Control fee leakage and filings for non-payers.", "C25, C23",
     "With the block policy on, Filed is refused for an overdue client unless an override exists.",
     [("Credit note, discount, write-off stay pending until Administrator approves", "API", "M", "FR-FE-08"),
      ("Overdue policy setting: reminder only, warn, or block Filed (default set after Q3)", "API", "M", "FR-FE-06"),
      ("One-time Administrator override with reason", "API", "S", "FR-FE-06"),
      ("Approval and policy screens", "WEB", "M", "FR-FE-06, FR-FE-08")]),

    ("C27", "R1", "Dashboards",
     "See the whole portfolio and drill into the list behind every number.", "C20, C25",
     "Each figure's list size equals the figure; staff never see counts that include other owners.",
     [("Administrator dashboard API and screen", "API", "M", "FR-RP-01"),
      ("Staff dashboard scoped to own clients", "WEB", "M", "FR-RP-02"),
      ("Tests: figure equals list; no cross-owner counts", "TEST", "S", "FR-RP-01, FR-RP-02")]),

    ("C28", "R1", "Reports and exports",
     "Reports for partners and auditors, with access rules and logging.", "C27, C04",
     "A staff export contains only permitted rows and writes a log entry.",
     [("Filing status by period; late filings with reasons", "API", "M", "FR-RP-03"),
      ("Fee aging; consultant turnaround; alert delivery", "API", "M", "FR-RP-03"),
      ("Export to spreadsheet and PDF under the same access rules", "API", "M", "FR-RP-04"),
      ("Every export written to the audit log", "API", "S", "FR-RP-04, FR-AU-01"),
      ("Reports screens", "WEB", "M", "FR-RP-03")]),

    ("C29", "R1", "Admin controls",
     "Rules are editable data and emergency access is controlled.", "C08, C06",
     "Opening a client you do not own asks for a reason and logs it; a due-date change needs no release.",
     [("Break-glass access with mandatory reason, flagged in audit", "API", "M", "FR-AC-05"),
      ("Admin settings: grace period, reminder interval, escalation point, link expiry, idle timeout", "API", "M", "NFR-MN-01, FR-AL-02, FR-IN-03"),
      ("Settings and break-glass screens", "WEB", "M", "")]),

    ("C30", "R1", "Security hardening",
     "Close the gaps before the independent test.", "C28",
     "Log review finds no PAN or bank detail; no unauthenticated file access.",
     [("Mask PAN and bank details on screen; log review", "API", "M", "NFR-SE-06"),
      ("Encryption at rest and key separation review", "OPS", "M", "NFR-SE-04"),
      ("Full authorization matrix against section 8 of the BRD", "TEST", "M", "FR-AC-01, NFR-SE-01"),
      ("Independent security test; fix critical and high findings", "OPS", "M", "NFR-SE-07")]),

    ("C31", "R1", "Resilience and performance",
     "Backups, monitoring and speed targets proven.", "C28",
     "Restore test recorded; load test meets targets; failed job alerts within 5 minutes.",
     [("Daily backups to a separate location; restore test script", "OPS", "M", "NFR-AV-02"),
      ("Monitoring and alerts for failed jobs, failed emails, error spikes", "OPS", "M", "NFR-OB-01"),
      ("Load test: 5,000 clients, 50,000 cycles", "TEST", "M", "NFR-PF-01, NFR-SC-01"),
      ("Accessibility (WCAG 2.1 AA) and cross-browser pass", "TEST", "M", "NFR-US-01")]),

    ("C32", "R1", "Migration, pilot and go-live",
     "Real data in, 20-client pilot, sign-off.", "C30, C31",
     "All M acceptance conditions pass; 2 weeks of pilot with no severity 1 defect; written sign-off.",
     [("Migration scripts per Q5 with count and 5% sample check", "OPS", "M", "BRD s15, Q5"),
      ("Load Q4 filing types and due-date rules", "OPS", "S", "Q4"),
      ("Pilot with 20 clients; defect triage", "OPS", "M", "BRD s14"),
      ("Sign-off pack with test evidence", "OPS", "S", "BRD s15")]),

    ("C33", "R2", "Client import",
     "Bulk load clients safely.", "C08",
     "Rows with errors are listed with reasons; valid rows save only after confirmation.",
     [("Spreadsheet upload and validation report", "API", "M", "FR-CL-05"),
      ("Confirm-and-save step; import screen", "WEB", "M", "FR-CL-05")]),

    ("C34", "R2", "Nil filings and phone follow-up",
     "Handle no-activity filings and call notes.", "C23",
     "Client confirmation is shown to the reviewer; a promised date raises a reminder.",
     [("Client confirms no activity; cycle skips to review", "API", "M", "FR-CY-03"),
      ("Phone-contact note with promised date and reminder", "API", "M", "FR-IN-05")]),

    ("C35", "R2", "Client messages and completion",
     "Two-way messages and a way to deliver the acknowledgement.", "C23, C15",
     "Client message notifies the owner; completion email is a manual draft per CR-03.",
     [("Portal reply stored against the cycle; owner notified", "API", "M", "FR-AL-07"),
      ("Completion draft with acknowledgement download link (manual send)", "API", "M", "FR-WF-06, CR-03"),
      ("Fee reminders at set intervals after due date; stop when paid", "JOB", "M", "FR-FE-07")]),

    ("C36", "R2", "WhatsApp or SMS",
     "Second channel with opt-out respected.", "C18",
     "An opted-out channel is never used; failure falls back to email.",
     [("WhatsApp or SMS provider adapter and approved templates", "API", "M", "INT-02, C-2, Q2"),
      ("Channel selection honours consent record; fallback to email", "API", "M", "FR-AL-08, INT-02")]),

    ("C37", "R2", "Package exceptions and access review",
     "Per-client filing changes with approval; quarterly certification.", "C08, C29",
     "A removal stays pending until approved; the access export shows the date generated.",
     [("Add or remove filings for one client; removals need Administrator approval", "API", "M", "FR-PK-07"),
      ("Access review export for quarterly certification", "API", "M", "FR-AC-04")]),
]

HEAD = """# Ledgerline - Development Plan

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

{{SUMMARY}}

Total: {{TOTAL}} tickets.

## Chunks

{{CHUNKS}}

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
"""


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    n = 0
    rows, out = [], []
    for cid, rel, title, goal, dep, done, tickets in CHUNKS:
        out.append(f"### {cid} - {title} ({rel})\n")
        out.append(f"**Goal:** {goal}  \n**Depends on:** {dep}\n")
        out.append("| Ticket | Type | Size | Task | BRD / CR |")
        out.append("| --- | --- | --- | --- | --- |")
        for t, typ, size, refs in tickets:
            n += 1
            tid = f"LL-{n:03d}"
            out.append(f"| {tid} | {typ} | {size} | {t} | {refs or '-'} |")
            rows.append([tid, cid, rel, title, t, typ, size, refs, "Todo"])
        out.append(f"\n**Done when:** {done}\n")
    summary = ["| Chunk | Release | Title | Tickets | Depends on |",
               "| --- | --- | --- | --- | --- |"]
    for cid, rel, title, goal, dep, done, tickets in CHUNKS:
        summary.append(f"| {cid} | {rel} | {title} | {len(tickets)} | {dep} |")
    body = (HEAD.replace("{{SUMMARY}}", "\n".join(summary))
                .replace("{{CHUNKS}}", "\n".join(out))
                .replace("{{TOTAL}}", str(n)))
    with open(os.path.join(root, "docs", "DEVELOPMENT_PLAN.md"), "w") as f:
        f.write(body)
    with open(os.path.join(root, "docs", "tickets.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticket_id", "chunk", "release", "chunk_title", "title",
                    "type", "size", "brd_refs", "status"])
        w.writerows(rows)
    print(f"{len(CHUNKS)} chunks, {n} tickets")


if __name__ == "__main__":
    main()
