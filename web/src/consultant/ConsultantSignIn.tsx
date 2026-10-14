import { useState, type SyntheticEvent } from "react";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import { api, messageFor } from "../api";
import { AuthLayout } from "../pages/AuthLayout";

/** The page a consultant's emailed link opens (NFR-SE-02). The link's token is in the URL
 * fragment (never sent to a server); it is taken out of the address bar at once, and posted
 * with the code that was shown on the screen that asked for the link. */
export function takeToken(): string | null {
  const match = /(?:^#|&)t=([A-Za-z0-9_-]{10,100})(?:&|$)/.exec(window.location.hash);
  if (match?.[1]) window.history.replaceState(null, "", window.location.pathname);
  return match?.[1] ?? null;
}

export function ConsultantSignIn({ token }: { token: string | null }) {
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: SyntheticEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/api/auth/consultant/finish", { method: "POST", json: { token, code: code.trim() } });
      window.location.replace("/");
    } catch (e) {
      setError(messageFor(e));
      setBusy(false);
    }
  }

  if (!token) {
    return (
      <AuthLayout title="Sign-in link" intro="Open the link from your email to sign in.">
        <Button href="/" variant="contained">Go to sign in</Button>
      </AuthLayout>
    );
  }
  return (
    <AuthLayout title="Enter your code" intro="Enter the code shown on the screen where you asked for this link.">
      <form onSubmit={(e) => void submit(e)} noValidate>
        <Stack spacing={2.5}>
          {error && <Alert severity="error">{error}</Alert>}
          <TextField
            label="Code"
            value={code}
            onChange={(e) => { setCode(e.target.value); }}
            slotProps={{ htmlInput: { inputMode: "numeric", autoComplete: "one-time-code", maxLength: 12 } }}
            autoFocus
            fullWidth
          />
          <Button type="submit" variant="contained" size="large" disabled={busy || code.replace(/\D/g, "").length !== 6}>Sign in</Button>
          {error && <Button href="/" variant="text">Ask for a new link</Button>}
        </Stack>
      </form>
    </AuthLayout>
  );
}
