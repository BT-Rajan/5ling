import { useState, type SyntheticEvent } from "react";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import { messageFor } from "../api";
import { useAuth } from "../auth/AuthProvider";
import { AuthLayout } from "./AuthLayout";

export function LoginPage() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: SyntheticEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password);
    } catch (e) {
      setError(messageFor(e));
      setPassword("");
      setBusy(false);
    }
  }

  return (
    <AuthLayout title="Sign in" intro="Use the email and password your administrator gave you.">
      <form onSubmit={(e) => void submit(e)} noValidate>
        <Stack spacing={2.5}>
          {error && <Alert severity="error">{error}</Alert>}
          <TextField
            label="Email"
            type="email"
            name="email"
            autoComplete="username"
            slotProps={{ htmlInput: { inputMode: "email", autoCapitalize: "none", spellCheck: false } }}
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
            }}
            required
            autoFocus
            fullWidth
          />
          <TextField
            label="Password"
            type="password"
            name="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
            }}
            required
            fullWidth
          />
          <Button type="submit" variant="contained" size="large" disabled={busy || !email || !password}>
            {busy ? "Signing in…" : "Sign in"}
          </Button>
        </Stack>
      </form>
    </AuthLayout>
  );
}
