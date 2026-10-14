import { useCallback, useEffect, useState, type SyntheticEvent } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import FormControlLabel from "@mui/material/FormControlLabel";
import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { ApiError, api, messageFor } from "../api";
import { useAuth, type EnrolInfo } from "../auth/AuthProvider";
import { RecoveryCodes } from "../components/RecoveryCodes";
import { AuthLayout } from "./AuthLayout";

type Step =
  | { kind: "password" }
  | { kind: "consultant" }
  | { kind: "code"; challenge: string }
  | { kind: "enrol"; challenge: string }
  | { kind: "codes"; codes: string[]; complete: () => void };

function isExpired(error: unknown): boolean {
  return error instanceof ApiError && error.code === "challenge_expired";
}

function PasswordStep({ onNext, notice }: { onNext: (step: Step) => void; notice: string | null }) {
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
      const next = await login(email.trim(), password);
      setPassword("");
      if (next.kind === "code") onNext({ kind: "code", challenge: next.challenge });
      else if (next.kind === "enrol") onNext({ kind: "enrol", challenge: next.challenge });
    } catch (e) {
      setError(messageFor(e));
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout title="Sign in" intro="Use the email and password your administrator gave you.">
      <form onSubmit={(e) => void submit(e)} noValidate>
        <Stack spacing={2.5}>
          {notice && !error && <Alert severity="info">{notice}</Alert>}
          {error && <Alert severity="error">{error}</Alert>}
          <TextField
            label="Email"
            type="email"
            name="email"
            autoComplete="username"
            slotProps={{ htmlInput: { inputMode: "email", autoCapitalize: "none", spellCheck: false } }}
            value={email}
            onChange={(e) => { setEmail(e.target.value); }}
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
            onChange={(e) => { setPassword(e.target.value); }}
            required
            fullWidth
          />
          <Button type="submit" variant="contained" size="large" disabled={busy || !email || !password}>
            {busy ? "Signing in…" : "Sign in"}
          </Button>
          <Button variant="text" onClick={() => { onNext({ kind: "consultant" }); }}>
            Consultant? Sign in with an emailed link
          </Button>
        </Stack>
      </form>
    </AuthLayout>
  );
}

/** NFR-SE-02: a consultant asks for a link; this screen shows the code the link will ask for. */
function ConsultantStep({ onBack }: { onBack: () => void }) {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState<{ code: string; minutes: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: SyntheticEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setSent(await api<{ code: string; minutes: number }>("/api/auth/consultant/start", { method: "POST", json: { email: email.trim() } }));
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  if (sent) {
    return (
      <AuthLayout title="Check your email" intro="If that address belongs to a consultant, we have emailed a sign-in link.">
        <Stack spacing={2.5}>
          <Typography>When you open the link, enter this code:</Typography>
          <Typography component="p" data-testid="consultant-code" sx={{ fontSize: "2rem", fontWeight: 700, letterSpacing: "0.15em", textAlign: "center" }}>{sent.code}</Typography>
          <Typography color="text.secondary">Keep this page open. The link and the code work once, for {sent.minutes} minutes.</Typography>
          <Button variant="text" onClick={onBack}>Back to sign in</Button>
        </Stack>
      </AuthLayout>
    );
  }
  return (
    <AuthLayout title="Consultant sign-in" intro="We will email you a link. This screen will show a code to enter when you open it.">
      <form onSubmit={(e) => void submit(e)} noValidate>
        <Stack spacing={2.5}>
          {error && <Alert severity="error">{error}</Alert>}
          <TextField
            label="Email"
            type="email"
            autoComplete="username"
            slotProps={{ htmlInput: { inputMode: "email", autoCapitalize: "none", spellCheck: false } }}
            value={email}
            onChange={(e) => { setEmail(e.target.value); }}
            required
            autoFocus
            fullWidth
          />
          <Button type="submit" variant="contained" size="large" disabled={busy || !email.trim()}>Email me a link</Button>
          <Button variant="text" onClick={onBack}>Back</Button>
        </Stack>
      </form>
    </AuthLayout>
  );
}

function CodeStep({ challenge, onBack }: { challenge: string; onBack: (message?: string) => void }) {
  const { verifyCode } = useAuth();
  const [recovery, setRecovery] = useState(false);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: SyntheticEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await verifyCode(challenge, code.trim());
    } catch (e) {
      if (isExpired(e)) {
        onBack(messageFor(e));
        return;
      }
      setError(messageFor(e));
      setCode("");
      setBusy(false);
    }
  }

  return (
    <AuthLayout
      title="Two-step sign-in"
      intro={recovery ? "Enter one of your recovery codes. Each works once." : "Enter the 6-digit code from your authenticator app."}
    >
      <form onSubmit={(e) => void submit(e)} noValidate>
        <Stack spacing={2.5}>
          {error && <Alert severity="error">{error}</Alert>}
          <TextField
            key={recovery ? "recovery" : "totp"}
            label={recovery ? "Recovery code" : "6-digit code"}
            name="code"
            autoComplete={recovery ? "off" : "one-time-code"}
            slotProps={{
              htmlInput: recovery
                ? { autoCapitalize: "none", spellCheck: false }
                : { inputMode: "numeric", pattern: "[0-9 ]*", maxLength: 7 },
            }}
            value={code}
            onChange={(e) => { setCode(e.target.value); }}
            required
            autoFocus
            fullWidth
          />
          <Button type="submit" variant="contained" size="large" disabled={busy || !code.trim()}>
            {busy ? "Checking…" : "Continue"}
          </Button>
          <Button
            variant="text"
            onClick={() => { setRecovery((r) => !r); setCode(""); setError(null); }}
          >
            {recovery ? "Use an authenticator code instead" : "Use a recovery code instead"}
          </Button>
          <Button variant="text" onClick={() => { onBack(); }}>
            Start again
          </Button>
        </Stack>
      </form>
    </AuthLayout>
  );
}

function EnrolStep({ challenge, onBack, onDone }: {
  challenge: string;
  onBack: (message?: string) => void;
  onDone: (codes: string[], complete: () => void) => void;
}) {
  const { startEnrolment, confirmEnrolment } = useAuth();
  const [info, setInfo] = useState<EnrolInfo | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    startEnrolment(challenge)
      .then((i) => { if (!cancelled) setInfo(i); })
      .catch((e: unknown) => {
        if (cancelled) return;
        if (isExpired(e)) onBack(messageFor(e));
        else setError(messageFor(e));
      });
    return () => { cancelled = true; };
  }, [challenge, startEnrolment, onBack]);

  async function submit(event: SyntheticEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const done = await confirmEnrolment(challenge, code.trim());
      onDone(done.recoveryCodes, done.complete);
    } catch (e) {
      if (isExpired(e)) {
        onBack(messageFor(e));
        return;
      }
      setError(messageFor(e));
      setCode("");
      setBusy(false);
    }
  }

  return (
    <AuthLayout title="Set up two-step sign-in" intro="Staff and Administrators sign in with a code from an authenticator app as well as a password.">
      <Stack spacing={2.5}>
        {error && <Alert severity="error">{error}</Alert>}
        <Typography component="ol" sx={{ m: 0, pl: 2.5, "& li": { mb: 1 } }}>
          <li>Install an authenticator app, such as Google Authenticator, Microsoft Authenticator or Aegis.</li>
          <li>Add Ledgerline by scanning the picture below.</li>
          <li>Type the 6-digit code the app shows.</li>
        </Typography>
        {info ? (
          <>
            <Box sx={{ display: "grid", placeItems: "center" }}>
              <Box
                component="img"
                src={info.qr}
                alt="QR code for adding Ledgerline to your authenticator app"
                sx={{ width: 200, height: 200, bgcolor: "#fff", borderRadius: 2, p: 1 }}
              />
            </Box>
            <Typography variant="body2" color="text.secondary">
              On this phone? <Link href={info.otpauth_uri}>Open in your authenticator app</Link>. Cannot scan? Enter this key by hand:
            </Typography>
            <Box sx={{ p: 1.5, borderRadius: 2, bgcolor: "primary.light", color: "primary.dark", fontFamily: "ui-monospace, monospace", textAlign: "center", userSelect: "all" }} data-testid="manual-key">
              {info.secret}
            </Box>
          </>
        ) : (
          !error && <Typography color="text.secondary">Preparing…</Typography>
        )}
        <form onSubmit={(e) => void submit(e)} noValidate>
          <Stack spacing={2.5}>
            <TextField
              label="6-digit code"
              name="code"
              autoComplete="one-time-code"
              slotProps={{ htmlInput: { inputMode: "numeric", pattern: "[0-9 ]*", maxLength: 7 } }}
              value={code}
              onChange={(e) => { setCode(e.target.value); }}
              required
              fullWidth
            />
            <Button type="submit" variant="contained" size="large" disabled={busy || !info || !code.trim()}>
              {busy ? "Checking…" : "Turn on two-step sign-in"}
            </Button>
            <Button variant="text" onClick={() => { onBack(); }}>Start again</Button>
          </Stack>
        </form>
      </Stack>
    </AuthLayout>
  );
}

function CodesStep({ codes, onContinue }: { codes: string[]; onContinue: () => void }) {
  const [saved, setSaved] = useState(false);
  return (
    <AuthLayout
      title="Save your recovery codes"
      intro="If you lose your phone, each of these codes lets you sign in once. They are shown only now."
    >
      <Stack spacing={2.5}>
        <RecoveryCodes codes={codes} />
        <FormControlLabel
          control={<Checkbox checked={saved} onChange={(e) => { setSaved(e.target.checked); }} />}
          label="I have saved these codes"
        />
        <Button variant="contained" size="large" disabled={!saved} onClick={onContinue}>
          Continue
        </Button>
      </Stack>
    </AuthLayout>
  );
}

export function LoginPage() {
  const { notice } = useAuth();
  const [step, setStep] = useState<Step>({ kind: "password" });
  const [carried, setCarried] = useState<string | null>(null);

  // Stable, because the enrolment step restarts if this function changes.
  const back = useCallback((message?: string) => {
    setCarried(message ?? null);
    setStep({ kind: "password" });
  }, []);

  if (step.kind === "consultant") return <ConsultantStep onBack={() => { back(); }} />;
  if (step.kind === "code") return <CodeStep challenge={step.challenge} onBack={back} />;
  if (step.kind === "enrol") {
    return (
      <EnrolStep
        challenge={step.challenge}
        onBack={back}
        onDone={(codes, complete) => { setStep({ kind: "codes", codes, complete }); }}
      />
    );
  }
  if (step.kind === "codes") return <CodesStep codes={step.codes} onContinue={step.complete} />;
  return <PasswordStep onNext={setStep} notice={carried ?? notice} />;
}
