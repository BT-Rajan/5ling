import { useState, type SyntheticEvent } from "react";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { messageFor } from "../api";
import { useAuth } from "../auth/AuthProvider";
import { AuthLayout } from "./AuthLayout";

const MIN = 12;

function Form({ forced }: { forced: boolean }) {
  const { changePassword, logout } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  const mismatch = again !== "" && again !== next;
  const tooShort = next !== "" && next.length < MIN;

  async function submit(event: SyntheticEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await changePassword(current, next);
      setDone(true);
      setCurrent("");
      setNext("");
      setAgain("");
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} noValidate>
      <Stack spacing={2.5}>
        {error && <Alert severity="error">{error}</Alert>}
        {done && !forced && <Alert severity="success">Your password has been changed.</Alert>}
        <TextField
          label={forced ? "Temporary password" : "Current password"}
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={(e) => {
            setCurrent(e.target.value);
          }}
          required
          fullWidth
        />
        <TextField
          label="New password"
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={(e) => {
            setNext(e.target.value);
          }}
          error={tooShort}
          helperText={`At least ${MIN} characters. A few random words work well.`}
          required
          fullWidth
        />
        <TextField
          label="Repeat new password"
          type="password"
          autoComplete="new-password"
          value={again}
          onChange={(e) => {
            setAgain(e.target.value);
          }}
          error={mismatch}
          helperText={mismatch ? "The two passwords do not match." : " "}
          required
          fullWidth
        />
        <Button
          type="submit"
          variant="contained"
          size="large"
          disabled={busy || !current || next.length < MIN || next !== again}
        >
          {busy ? "Saving…" : "Change password"}
        </Button>
        {forced && (
          <Button
            variant="text"
            onClick={() => {
              void logout();
            }}
          >
            Sign out
          </Button>
        )}
      </Stack>
    </form>
  );
}

/** `forced` is the first-sign-in screen; otherwise it is the page reached from the account menu. */
export function ChangePasswordPage({ forced = false }: { forced?: boolean }) {
  if (forced) {
    return (
      <AuthLayout title="Choose a new password" intro="Your administrator gave you a temporary password. Choose your own to continue.">
        <Form forced />
      </AuthLayout>
    );
  }
  return (
    <>
      <Typography variant="h1" component="h1" sx={{ mb: 3, display: { xs: "none", md: "block" } }}>
        Change password
      </Typography>
      <Stack sx={{ maxWidth: 440 }}>
        <Form forced={false} />
      </Stack>
    </>
  );
}
