import { useEffect, useState } from "react";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import { Link as RouterLink, useLocation } from "react-router-dom";
import { api, messageFor } from "../api";
import type { AuditCheck } from "../audit";
import { useUser } from "../auth/AuthProvider";
import { formatIST } from "../time";

interface JobRun {
  id: number;
  status: "running" | "ok" | "failed" | "skipped";
  finished_at: string | null;
  clients_failed: number;
  cycles_created: number;
  error: string | null;
}

/**
 * Problems the Administrator (and the Partners, who hold that role) must hear about the same day,
 * shown on every screen until fixed:
 * - a failed audit log check (BRD FR-AU-03);
 * - a cycle generator run that could not do every client (BRD FR-CY-05). Running it again is safe.
 */
export function AuditAlarm() {
  const user = useUser();
  const { pathname } = useLocation();
  const [failedCheck, setFailedCheck] = useState<AuditCheck | null>(null);
  const [failedRun, setFailedRun] = useState<JobRun | null>(null);
  const [rerun, setRerun] = useState<{ busy: boolean; message: string | null }>({ busy: false, message: null });
  const admin = user.role === "administrator";

  useEffect(() => {
    if (!admin) return;
    let live = true;
    api<{ latest: AuditCheck | null }>("/api/admin/audit/status")
      .then(({ latest }) => { if (live) setFailedCheck(latest && !latest.ok ? latest : null); })
      .catch(() => undefined); // a backstop: the daily jobs also log failures loudly
    api<{ latest: JobRun | null }>("/api/admin/jobs/cycle-generator")
      .then(({ latest }) => { if (live) setFailedRun(latest?.status === "failed" ? latest : null); })
      .catch(() => undefined);
    return () => { live = false; };
  }, [admin, pathname]);

  async function runAgain() {
    setRerun({ busy: true, message: null });
    try {
      const result = await api<JobRun>("/api/admin/jobs/cycle-generator/run", { method: "POST", timeoutMs: 300_000 });
      if (result.status === "ok") {
        setFailedRun(null);
        setRerun({ busy: false, message: null });
      } else {
        setFailedRun(result.status === "failed" ? result : failedRun);
        setRerun({ busy: false, message: result.status === "skipped" ? "Another run is in progress. Try again in a few minutes." : "It failed again." });
      }
    } catch (e) {
      setRerun({ busy: false, message: messageFor(e) });
    }
  }

  const showCheck = failedCheck && !pathname.startsWith("/audit");
  if (!showCheck && !failedRun) return null;
  return (
    <Stack spacing={2} sx={{ mb: 3 }}>
      {showCheck && (
        <Alert severity="error" action={<Button color="inherit" component={RouterLink} to="/audit">Open audit log</Button>}>
          <AlertTitle>The audit log may have been tampered with</AlertTitle>
          The check on {formatIST(failedCheck.finished_at)} failed
          {failedCheck.first_bad_seq ? ` at entry ${String(failedCheck.first_bad_seq)}` : ""}: {failedCheck.problem_text ?? failedCheck.problem}
        </Alert>
      )}
      {failedRun && (
        <Alert severity="error" action={<Button color="inherit" disabled={rerun.busy} onClick={() => void runAgain()}>{rerun.busy ? "Running…" : "Run again"}</Button>}>
          <AlertTitle>Some filing cycles were not created</AlertTitle>
          The cycle generator run of {failedRun.finished_at ? formatIST(failedRun.finished_at) : "today"} could not finish {failedRun.clients_failed} client
          {failedRun.clients_failed === 1 ? "" : "s"}{failedRun.error ? ` (${failedRun.error})` : ""}. Running it again is safe: it only makes what is missing.
          {rerun.message && <><br />{rerun.message}</>}
        </Alert>
      )}
    </Stack>
  );
}
