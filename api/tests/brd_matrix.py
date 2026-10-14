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
    ("GET", "/api/stages"),
    ("GET", "/api/tickets/{ticket}"),
    ("GET", "/api/clients/{client_id}/tickets"),
    ("GET", "/api/tickets"),
    ("GET", "/api/tickets/assignees"),
)

CAPABILITIES: tuple[Capability, ...] = (
    # One search route serves both: the server narrows Staff to their own clients.
    Capability("See all clients", "Y", "-", "-", "-", "C08", routes=CLIENT_READS),
    Capability("See assigned clients only", "-", "Own", "-", "-", "C08", routes=CLIENT_READS),
    Capability("See assigned task only", "-", "-", "Task", "-", "C21"),
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
            ("PATCH", "/api/clients/{client_id}"),
            ("GET", "/api/clients/{client_id}/history"),
            # Moving a cycle on; later chunks add maker-checker (C22) and filing proof (C23).
            ("POST", "/api/tickets/{ticket}/transition"),
            # Ad hoc task tickets on a client (CR-02).
            ("POST", "/api/tickets"),
            ("PATCH", "/api/tickets/{ticket}"),
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
    Capability("Assign preparers", "Y", "Own", "-", "-", "C21"),
    Capability("Upload documents", "Y", "Own", "Task", "Own link", "C16"),
    Capability("Download documents", "Y", "Own", "Task", "Own link", "C16"),
    Capability("Approve a filing (as checker)", "Y", "Own, not as preparer", "-", "-", "C22"),
    Capability("Mark filed", "Y", "Own", "-", "-", "C23"),
    Capability("See fee ledger", "Y", "Own", "-", "-", "C25"),
    Capability("Record payments", "Y", "Own", "-", "-", "C25"),
    Capability("Approve credit, discount, write-off", "Y", "-", "-", "-", "C26"),
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
    Capability("Export data", "Y", "Own, logged", "-", "-", "C28"),
)

# Routes everyone uses on their own account; they are not capabilities in the BRD matrix.
SELF_SERVICE: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/auth/me"),
        ("POST", "/api/auth/logout"),
        ("POST", "/api/auth/change-password"),
        ("POST", "/api/auth/mfa/recovery-codes"),
    }
)
