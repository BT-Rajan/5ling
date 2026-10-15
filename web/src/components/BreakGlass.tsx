import { useState } from "react";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { Link as RouterLink } from "react-router-dom";
import { api, ApiError, messageFor } from "../api";

/** The client id from a "reason required" refusal (FR-AC-05), or null for any other error. */
export function reasonRequiredFor(error: unknown): number | null {
  if (!(error instanceof ApiError) || error.code !== "reason_required") return null;
  const body = error.body as { error?: { client_id?: unknown } } | null;
  const id = body?.error?.client_id;
  return typeof id === "number" ? id : null;
}

/** FR-AC-05: an Administrator says why before opening a client they do not own. */
export function BreakGlass({ clientId, onOpened }: { clientId: number; onOpened: () => void }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function open() {
    setBusy(true);
    setError(null);
    try {
      await api(`/api/clients/${String(clientId)}/break-glass`, { method: "POST", json: { reason: reason.trim() } });
      onOpened();
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Paper component="form" aria-label="Reason for opening this client" onSubmit={(e) => { e.preventDefault(); void open(); }} noValidate variant="outlined" sx={{ p: 3, borderRadius: 3, maxWidth: 560 }}>
      <Typography variant="h5" component="h1" sx={{ mb: 1 }}>This client is not yours</Typography>
      <Typography color="text.secondary" sx={{ mb: 2 }}>
        Say why you are opening client {clientId}. Your reason is recorded and flagged in the audit log, and the client stays open to you for a few hours.
      </Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <Stack spacing={2}>
        <TextField label="Reason" value={reason} onChange={(e) => { setReason(e.target.value); }} slotProps={{ htmlInput: { maxLength: 500 } }} disabled={busy} autoFocus multiline minRows={2} />
        <Stack direction="row" sx={{ gap: 1 }}>
          <Button type="submit" variant="contained" disabled={busy || reason.trim().length < 5}>Open the client</Button>
          <Button component={RouterLink} to="/clients" disabled={busy}>Go back</Button>
        </Stack>
      </Stack>
    </Paper>
  );
}
