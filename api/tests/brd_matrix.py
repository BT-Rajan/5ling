"""BRD section 8 (role permission matrix), as a checklist that grows with the build.

Every capability the BRD lists is tracked here. `built_in` says which chunk of the development
plan builds it; `routes` lists the endpoints that implement it once built. When a chunk lands, its
capabilities get their routes and the harness in test_authz_matrix.py starts testing them against
every role. A capability cannot be dropped silently: the count is asserted.

Cells: Y = allowed, Own = only what the person owns, Task = only the single assigned cycle,
"-" = not allowed, "Own link" = by the client's private link (not an account).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    name: str
    admin: str
    staff: str
    consultant: str
    client: str
    built_in: str
    routes: tuple[tuple[str, str], ...] = ()

    def allowed(self, who: str) -> bool:
        return bool(getattr(self, who) != "-")


# Reading a client's records: one route each, narrowed to their own clients for Staff.
CLIENT_READS: tuple[tuple[str, str], ...] = (
    ("GET", "/api/clients"),
    # The daily work list and its count report (FR-IN-04), narrowed to their own for Staff.
    ("GET", "/api/worklist"),
    ("GET", "/api/reports/worklist-counts"),
    # The dashboard and the list behind each figure (FR-RP-01, FR-RP-02).
    ("GET", "/api/dashboard"),
    ("GET", "/api/dashboard/list"),
    # Reports, on screen (FR-RP-03); exporting them is "Export data".
    ("GET", "/api/reports/{report}"),
    ("GET", "/api/stages"),
    ("GET", "/api/tickets/{ticket}"),
    ("GET", "/api/clients/{client_id}/tickets"),
    ("GET", "/api/tickets"),
    ("GET", "/api/tickets/assignees"),
)

CAPABILITIES: tuple[Capability, ...] = (
    # One search route serves both: the server narrows Staff to their own clients.
    Capability(
        "See all clients",
        "Y",
        "-",
        "-",
        "-",
        "C08",
        # Opening a client the Administrator does not own takes a reason (FR-AC-05, C29).
        routes=(
            *CLIENT_READS,
            ("POST", "/api/clients/{client_id}/break-glass"),
            ("GET", "/api/admin/break-glass"),
        ),
    ),
    Capability("See assigned clients only", "-", "Own", "-", "-", "C08", routes=CLIENT_READS),
    Capability(
        "See assigned task only",
        "-",
        "-",
        "Task",
        "-",
        "C21",
        # A consultant's open assignments, their documents, and uploading work (FR-WF-02).
        routes=(
            ("GET", "/api/consultant/assignments"),
            ("GET", "/api/consultant/assignments/{assignment_id}"),
            ("POST", "/api/uploads/consultant/assignments/{assignment_id}"),
            # Sending their work for review (FR-WF-03).
            ("POST", "/api/consultant/assignments/{assignment_id}/submit"),
        ),
    ),
    Capability(
        "Create and edit clients",
        "Y",
        "Own / create",
        "-",
        "-",
        "C07",
        routes=(
            ("GET", "/api/clients/options"),
            ("POST", "/api/clients"),
            ("GET", "/api/clients/{client_id}"),
            ("PUT", "/api/clients/{client_id}/contacts/{contact_id}/consent"),
            # Asking for a client-specific fee (FR-FE-01); an Administrator decides.
            ("POST", "/api/clients/{client_id}/fee-overrides"),
            # The client's own overdue limit (FR-FE-06, BRD Q3).
            ("PUT", "/api/clients/{client_id}/overdue-limit"),
            ("PATCH", "/api/clients/{client_id}"),
            ("GET", "/api/clients/{client_id}/history"),
            # Moving a cycle on; approve is refused to the preparer (C22); filing needs proof (C23).
            ("POST", "/api/tickets/{ticket}/transition"),
            # Ad hoc task tickets on a client (CR-02).
            ("POST", "/api/tickets"),
            ("PATCH", "/api/tickets/{ticket}"),
            # The client's private link for a cycle (FR-AL-02).
            ("GET", "/api/tickets/{ticket}/links"),
            ("POST", "/api/tickets/{ticket}/links"),
            ("POST", "/api/tickets/{ticket}/links/{link_id}/withdraw"),
            # Client updates: one draft per state change, sent by hand (CR-03).
            ("GET", "/api/tickets/{ticket}/updates"),
            # Client emails on a ticket, and following up failed ones (FR-AL-06).
            ("GET", "/api/tickets/{ticket}/emails"),
            ("POST", "/api/emails/{email_id}/followed-up"),
            ("GET", "/api/clients/{client_id}/updates"),
            ("POST", "/api/updates/{email_id}/preview"),
            ("POST", "/api/updates/{email_id}/send"),
            # Documents on a cycle, for staff (FR-AL-04, FR-AL-05). The BRD's upload and download
            # rows also cover consultants (C21) and clients by link (C17), which add their own.
            ("GET", "/api/tickets/{ticket}/checklist"),
            ("GET", "/api/tickets/{ticket}/documents"),
            ("POST", "/api/uploads/tickets/{ticket}"),
        ),
    ),
    Capability(
        "Create packages and calendars",
        "Y",
        "-",
        "-",
        "-",
        "C05",
        routes=(
            ("GET", "/api/admin/filings"),
            ("POST", "/api/admin/filings"),
            ("GET", "/api/admin/filings/{filing_id}"),
            ("PATCH", "/api/admin/filings/{filing_id}"),
            ("PUT", "/api/admin/filings/{filing_id}/calendar/draft"),
            ("DELETE", "/api/admin/filings/{filing_id}/calendar/draft"),
            ("POST", "/api/admin/filings/{filing_id}/calendar/publish"),
            ("GET", "/api/admin/packages"),
            ("POST", "/api/admin/packages"),
            ("GET", "/api/admin/packages/{package_id}"),
            ("PATCH", "/api/admin/packages/{package_id}"),
            ("PUT", "/api/admin/packages/{package_id}/draft"),
            ("DELETE", "/api/admin/packages/{package_id}/draft"),
            ("POST", "/api/admin/packages/{package_id}/publish"),
            # The editable rules: grace, reminders, escalation, link expiry, idle timeout
            # (NFR-MN-01, C29).
            ("GET", "/api/admin/rules"),
            ("PUT", "/api/admin/rules"),
            # Fee terms: client-specific fees to approve, and tax rates (FR-FE-01, FR-FE-09).
            ("GET", "/api/admin/fee-overrides"),
            ("POST", "/api/admin/fee-overrides/{override_id}/decision"),
            ("GET", "/api/admin/tax-rates"),
            ("POST", "/api/admin/tax-rates"),
            ("POST", "/api/admin/filings/preview"),
            ("GET", "/api/admin/holidays"),
            ("POST", "/api/admin/holidays"),
            ("DELETE", "/api/admin/holidays/{day}"),
            ("GET", "/api/admin/lead-times"),
            ("PUT", "/api/admin/lead-times"),
            # Cycles are made from packages and calendars; the Administrator watches the job.
            ("GET", "/api/admin/jobs/cycle-generator"),
            ("POST", "/api/admin/jobs/cycle-generator/run"),
            # Government extensions move the dates of open cycles (FR-PK-06).
            ("GET", "/api/admin/extensions"),
            ("GET", "/api/admin/extensions/periods"),
            ("POST", "/api/admin/extensions/preview"),
            ("POST", "/api/admin/extensions"),
        ),
    ),
    Capability(
        "Assign preparers",
        "Y",
        "Own",
        "-",
        "-",
        "C21",
        routes=(
            ("GET", "/api/assignment-candidates"),
            ("GET", "/api/tickets/{ticket}/assignment"),
            ("POST", "/api/tickets/{ticket}/assignment"),
            ("POST", "/api/tickets/{ticket}/assignment/withdraw"),
        ),
    ),
    Capability("Upload documents", "Y", "Own", "Task", "Own link", "C16"),
    Capability(
        "Download documents",
        "Y",
        "Own",
        "Task",
        "Own link",
        "C16",
        # A short-lived personal link, then the file; checked again when used (NFR-SE-05).
        routes=(
            ("POST", "/api/documents/versions/{version_id}/link"),
            ("GET", "/api/files/{token}"),
        ),
    ),
    Capability(
        "Approve a filing (as checker)",
        "Y",
        "Own, not as preparer",
        "-",
        "-",
        "C22",
        # The approve action; the server refuses it to anyone who prepared the work.
        routes=(("POST", "/api/tickets/{ticket}/transition"),),
    ),
    Capability(
        "Mark filed",
        "Y",
        "Own",
        "-",
        "-",
        "C23",
        # With the acknowledgement and reference number, in one request (FR-WF-05).
        routes=(("POST", "/api/uploads/tickets/{ticket}/filing"),),
    ),
    Capability(
        "See fee ledger",
        "Y",
        "Own",
        "-",
        "-",
        "C25",
        # The fee plan, invoices, payments, balances and fee status (C24, C25).
        routes=(("GET", "/api/clients/{client_id}/fees"),),
    ),
    Capability(
        "Record payments",
        "Y",
        "Own",
        "-",
        "-",
        "C25",
        # Payments and part payments, and reversing entries (FR-FE-03).
        routes=(
            ("POST", "/api/invoices/{invoice_id}/payments"),
            ("POST", "/api/ledger/{entry_id}/reverse"),
            # Asking for a credit note, discount or write-off (FR-FE-08).
            ("POST", "/api/invoices/{invoice_id}/adjustments"),
        ),
    ),
    Capability(
        "Approve credit, discount, write-off",
        "Y",
        "-",
        "-",
        "-",
        "C26",
        routes=(
            ("GET", "/api/admin/fee-adjustments"),
            ("POST", "/api/admin/fee-adjustments/{adjustment_id}/decision"),
            # The overdue policy and its one-time override (FR-FE-06).
            ("GET", "/api/admin/fee-policy"),
            ("PUT", "/api/admin/fee-policy"),
            ("POST", "/api/tickets/{ticket}/fee-waiver"),
        ),
    ),
    Capability(
        "Manage people and access",
        "Y",
        "-",
        "-",
        "-",
        "C01",
        routes=(
            ("GET", "/api/admin/users"),
            ("POST", "/api/admin/users"),
            ("POST", "/api/admin/users/{user_id}/disable"),
            ("POST", "/api/admin/users/{user_id}/enable"),
            ("POST", "/api/admin/users/{user_id}/reset-password"),
            ("POST", "/api/admin/users/{user_id}/reset-mfa"),
        ),
    ),
    Capability(
        "View audit log",
        "Y",
        "-",
        "-",
        "-",
        "C04",
        # Checking and exporting the log are part of reviewing it (FR-AU-03, FR-AU-04).
        routes=(
            ("GET", "/api/admin/audit/entries"),
            ("GET", "/api/admin/audit/events"),
            ("GET", "/api/admin/audit/status"),
            ("GET", "/api/admin/audit/checks"),
            ("POST", "/api/admin/audit/verify"),
            ("POST", "/api/admin/audit/export"),
            # The outbox is the record of every email sent, to every client (INT-01).
            ("GET", "/api/admin/emails"),
            ("GET", "/api/admin/emails/{email_id}"),
            ("POST", "/api/admin/emails/test"),
        ),
    ),
    Capability(
        "Export data",
        "Y",
        "Own, logged",
        "-",
        "-",
        "C28",
        # Every report as a spreadsheet or PDF, narrowed to their own for Staff, and audited.
        routes=(("POST", "/api/reports/{report}/export"),),
    ),
)

# Routes everyone uses on their own account; they are not capabilities in the BRD matrix.
SELF_SERVICE: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/auth/me"),
        ("POST", "/api/auth/logout"),
        ("POST", "/api/auth/change-password"),
        ("POST", "/api/auth/mfa/recovery-codes"),
        # Each person's own notifications (FR-IN-01 to FR-IN-03), narrowed to their clients.
        ("GET", "/api/notifications"),
        ("GET", "/api/notifications/count"),
        ("POST", "/api/notifications/{notification_id}/read"),
        ("POST", "/api/notifications/{notification_id}/dismiss"),
    }
)
