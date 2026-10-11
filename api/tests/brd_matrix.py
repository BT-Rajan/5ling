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


CAPABILITIES: tuple[Capability, ...] = (
    Capability("See all clients", "Y", "-", "-", "-", "C08"),
    Capability("See assigned clients only", "-", "Own", "-", "-", "C08"),
    Capability("See assigned task only", "-", "-", "Task", "-", "C21"),
    Capability("Create and edit clients", "Y", "Own / create", "-", "-", "C07"),
    Capability("Create packages and calendars", "Y", "-", "-", "-", "C05"),
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
    Capability("View audit log", "Y", "-", "-", "-", "C04"),
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
