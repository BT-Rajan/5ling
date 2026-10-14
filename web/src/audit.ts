/** Shapes the audit endpoints return, and plain-words labels for what they contain. */

export interface AuditCheck {
  id: number;
  started_at: string;
  finished_at: string;
  trigger: "scheduled" | "manual" | "export";
  requested_by_id: number | null;
  ok: boolean;
  entries_checked: number;
  last_seq: number;
  first_bad_seq: number | null;
  problem: string | null;
  problem_text: string | null;
}

export interface AuditEntry {
  seq: number;
  occurred_at: string;
  event: string;
  actor_user_id: number | null;
  actor_name: string | null;
  actor_role: string | null;
  ip: string | null;
  request_id: string | null;
  target_type: string | null;
  target_id: string | null;
  details: Record<string, unknown>;
  entry_hash: string;
}

export interface AuditPage {
  items: AuditEntry[];
  next_before_seq: number | null;
}

const LABELS: Record<string, string> = {
  "login.succeeded": "Signed in",
  "login.failed": "Sign-in failed",
  "login.password_ok": "Password accepted, code requested",
  "login.locked": "Account locked after failed attempts",
  "login.throttled": "Too many sign-in attempts from one address",
  "logout": "Signed out",
  "mfa.failed": "Wrong two-step code",
  "mfa.enrolled": "Set up two-step sign-in",
  "mfa.recovery_code_used": "Used a recovery code",
  "mfa.recovery_codes_replaced": "Replaced recovery codes",
  "mfa.recovery_codes_denied": "Recovery codes refused (wrong password)",
  "mfa.challenge_rejected": "Expired or unknown sign-in step",
  "password.changed": "Changed password",
  "password.change_failed": "Password change refused (wrong password)",
  "session.idle_expired": "Signed out after inactivity",
  "access.denied": "Refused: no access",
  "csrf.rejected": "Refused: request could not be verified",
  "origin.rejected": "Refused: came from another site",
  "request.denied": "Refused request",
  "user.created": "Added a person",
  "user.disabled": "Disabled a person",
  "user.enabled": "Enabled a person",
  "user.password_reset": "Reset a password",
  "user.mfa_reset": "Reset two-step sign-in",
  "audit.check_passed": "Audit log check passed",
  "audit.check_failed": "Audit log check FAILED",
  "audit.exported": "Exported the audit log",
};

export function eventLabel(event: string): string {
  return LABELS[event] ?? event;
}

const TRIGGER: Record<AuditCheck["trigger"], string> = {
  scheduled: "daily check",
  manual: "check on request",
  export: "check with an export",
};

export function triggerLabel(trigger: AuditCheck["trigger"]): string {
  return TRIGGER[trigger];
}
