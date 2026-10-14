import { useEffect, useState } from "react";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Button from "@mui/material/Button";
import { Link as RouterLink, useLocation } from "react-router-dom";
import { api } from "../api";
import type { AuditCheck } from "../audit";
import { useUser } from "../auth/AuthProvider";
import { formatIST } from "../time";

/**
 * BRD FR-AU-03: a failed audit log check alerts the Administrator and the Partners (who hold the
 * Administrator role). Shown on every screen until a later check passes.
 */
export function AuditAlarm() {
  const user = useUser();
  const { pathname } = useLocation();
  const [failed, setFailed] = useState<AuditCheck | null>(null);
  const admin = user.role === "administrator";

  useEffect(() => {
    if (!admin) return;
    let live = true;
    api<{ latest: AuditCheck | null }>("/api/admin/audit/status")
      .then(({ latest }) => {
        if (live) setFailed(latest && !latest.ok ? latest : null);
      })
      .catch(() => {
        // The banner is a backstop; the daily job also logs the failure loudly.
      });
    return () => {
      live = false;
    };
  }, [admin, pathname]);

  if (!failed || pathname.startsWith("/audit")) return null;
  return (
    <Alert
      severity="error"
      sx={{ mb: 3 }}
      action={
        <Button color="inherit" component={RouterLink} to="/audit">
          Open audit log
        </Button>
      }
    >
      <AlertTitle>The audit log may have been tampered with</AlertTitle>
      The check on {formatIST(failed.finished_at)} failed
      {failed.first_bad_seq ? ` at entry ${failed.first_bad_seq}` : ""}: {failed.problem_text ?? failed.problem}
    </Alert>
  );
}
