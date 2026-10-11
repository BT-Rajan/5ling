import { useState, type SyntheticEvent } from "react";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogContentText from "@mui/material/DialogContentText";
import DialogTitle from "@mui/material/DialogTitle";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { api, messageFor } from "../api";
import { useAuth, useUser } from "../auth/AuthProvider";
import { RecoveryCodes } from "../components/RecoveryCodes";
import { PasswordForm } from "./ChangePasswordPage";

const LOW = 3;

function NewCodesDialog({ onClose }: { onClose: () => void }) {
  const { refreshSession } = useAuth();
  const [password, setPassword] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: SyntheticEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const out = await api<{ recovery_codes: string[] }>("/api/auth/mfa/recovery-codes", {
        method: "POST",
        json: { current_password: password },
      });
      setPassword("");
      setCodes(out.recovery_codes);
      await refreshSession();
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open onClose={busy ? undefined : onClose} fullWidth maxWidth="xs" aria-labelledby="new-codes-title">
      <DialogTitle id="new-codes-title">{codes ? "Your new recovery codes" : "Make new recovery codes"}</DialogTitle>
      {codes ? (
        <>
          <DialogContent>
            <DialogContentText sx={{ mb: 2 }}>The old codes no longer work. These are shown only now.</DialogContentText>
            <RecoveryCodes codes={codes} />
          </DialogContent>
          <DialogActions sx={{ px: 3, pb: 2 }}>
            <Button variant="contained" onClick={onClose}>Done</Button>
          </DialogActions>
        </>
      ) : (
        <form onSubmit={(e) => void submit(e)} noValidate>
          <DialogContent>
            <Stack spacing={2}>
              <DialogContentText>Your old codes stop working. Enter your password to continue.</DialogContentText>
              {error && <Alert severity="error">{error}</Alert>}
              <TextField
                label="Password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => { setPassword(e.target.value); }}
                required
                autoFocus
                fullWidth
              />
            </Stack>
          </DialogContent>
          <DialogActions sx={{ px: 3, pb: 2 }}>
            <Button onClick={onClose} disabled={busy}>Cancel</Button>
            <Button type="submit" variant="contained" disabled={busy || !password}>
              {busy ? "Working…" : "Make new codes"}
            </Button>
          </DialogActions>
        </form>
      )}
    </Dialog>
  );
}

export function AccountSecurityPage() {
  const user = useUser();
  const { state } = useAuth();
  const left = state.status === "authenticated" ? state.recoveryCodesLeft : null;
  const [making, setMaking] = useState(false);
  const twoStep = user.role === "administrator" || user.role === "staff";

  return (
    <>
      <Typography variant="h1" component="h1" sx={{ mb: 3, display: { xs: "none", md: "block" } }}>
        Account security
      </Typography>
      <Stack spacing={3} sx={{ maxWidth: 480 }}>
        <Paper variant="outlined" sx={{ p: { xs: 2, md: 3 } }}>
          <Typography variant="h2" component="h2" sx={{ mb: 2 }}>Password</Typography>
          <PasswordForm forced={false} />
        </Paper>

        {twoStep && (
          <Paper variant="outlined" sx={{ p: { xs: 2, md: 3 } }}>
            <Typography variant="h2" component="h2" sx={{ mb: 1 }}>Two-step sign-in</Typography>
            <Typography color="text.secondary" sx={{ mb: 2 }}>
              On. You sign in with a code from your authenticator app as well as your password.
            </Typography>
            <Typography sx={{ mb: left !== null && left <= LOW ? 1 : 2 }}>
              Recovery codes left: <strong>{left ?? 0}</strong>
            </Typography>
            {left !== null && left <= LOW && (
              <Alert severity="warning" sx={{ mb: 2 }}>
                {left === 0 ? "You have no recovery codes left." : "You are running low on recovery codes."} Make new ones so you can still sign in if you lose your phone.
              </Alert>
            )}
            <Button variant="outlined" onClick={() => { setMaking(true); }}>Make new recovery codes</Button>
          </Paper>
        )}
      </Stack>
      {making && <NewCodesDialog onClose={() => { setMaking(false); }} />}
    </>
  );
}
