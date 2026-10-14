import { useEffect, useRef, useState, type ReactNode } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import Paper from "@mui/material/Paper";
import Typography from "@mui/material/Typography";
import { ApiError, api, messageFor } from "../api";
import { formatDay, formatIST } from "../time";
import { Checklist } from "./Checklist";

/** The client's side (BRD FR-AL-02): one cycle, reached by a private link, phone first.
 *
 * The link carries its token in the URL fragment (#t=...), which browsers never send to a server.
 * It is read once, removed from the address bar (so it is not left in history or shared by
 * copying the address), and posted to the server, which uses it up and sets a portal cookie. */

export interface Portal {
  client_name: string;
  client_id: number;
  ticket: string;
  filing_code: string;
  filing_name: string;
  period_label: string;
  due_date: string;
  stage_label: string;
  expires_at: string;
  csrf_token: string;
}

type View =
  | { kind: "loading" }
  | { kind: "ready"; portal: Portal }
  | { kind: "problem"; message: string; token: string | null; canRequestNew: boolean }
  | { kind: "no_link" }
  | { kind: "signed_out" };

function takeToken(): string | null {
  const match = /(?:^#|&)t=([A-Za-z0-9_-]{1,100})(?:&|$)/.exec(window.location.hash);
  if (!match?.[1]) return null;
  window.history.replaceState(null, "", window.location.pathname);
  return match[1];
}

function Frame({ children }: { children: ReactNode }) {
  return (
    <Box component="main" sx={{ minHeight: "100dvh", bgcolor: "background.default", px: 2, py: { xs: 3, sm: 6 } }}>
      <Box sx={{ maxWidth: 560, mx: "auto" }}>
        <Typography variant="overline" color="text.secondary" component="p" sx={{ mb: 1 }}>Ledgerline · private page</Typography>
        {children}
      </Box>
    </Box>
  );
}

function NewLink({ token, message }: { token: string | null; message: string }) {
  const [busy, setBusy] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function ask() {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      const sent = await api<{ sent_to: string }>("/api/portal/request-link", { method: "POST", json: { token } });
      setSentTo(sent.sent_to);
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Alert severity="warning" sx={{ mb: 2 }}>{message}</Alert>
      {sentTo ? (
        <Alert severity="success">We have emailed a new link to {sentTo}. It may take a few minutes to arrive.</Alert>
      ) : (
        <>
          {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
          <Button variant="contained" size="large" fullWidth onClick={() => void ask()} disabled={busy}>Email me a new link</Button>
          <Typography color="text.secondary" variant="body2" sx={{ mt: 1 }}>It goes to the email address we have for you, not to whoever has this link.</Typography>
        </>
      )}
    </>
  );
}

function PortalHome({ portal, onSignedOut }: { portal: Portal; onSignedOut: () => void }) {
  const [busy, setBusy] = useState(false);

  async function signOut() {
    setBusy(true);
    try {
      await api("/api/portal/logout", { method: "POST", headers: { "X-CSRF-Token": portal.csrf_token } });
    } catch {
      // Signed out or not, this page stops showing anything.
    }
    onSignedOut();
  }

  return (
    <>
      <Typography variant="h1" component="h1" sx={{ fontSize: { xs: "1.6rem", sm: "2rem" }, mb: 2 }}>Hello, {portal.client_name}</Typography>
      <Paper variant="outlined" sx={{ p: 2, borderRadius: 3, mb: 2 }}>
        <Typography variant="h2" component="h2" sx={{ fontSize: "1.25rem", mb: 1 }}>{portal.filing_code} · {portal.period_label}</Typography>
        <Box component="dl" sx={{ display: "grid", gridTemplateColumns: "max-content 1fr", columnGap: 2, rowGap: 0.75, m: 0, "& dt": { color: "text.secondary" }, "& dd": { m: 0 } }}>
          <dt>Filing</dt><dd>{portal.filing_name}</dd>
          <dt>Due</dt><dd>{formatDay(portal.due_date)}</dd>
          <dt>Status</dt><dd data-testid="portal-stage">{portal.stage_label}</dd>
          <dt>Reference</dt><dd>{portal.ticket} · client ID {portal.client_id}</dd>
        </Box>
      </Paper>
      <Checklist csrf={portal.csrf_token} />
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        This private page works on this device until {formatIST(portal.expires_at)}. Please do not share it.
      </Typography>
      <Button variant="outlined" onClick={() => void signOut()} disabled={busy}>Sign out on this device</Button>
    </>
  );
}

export function PortalApp() {
  const [view, setView] = useState<View>({ kind: "loading" });
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return; // React StrictMode runs effects twice; a link opens only once
    started.current = true;
    const token = takeToken();
    const request = token
      ? api<Portal>("/api/portal/open", { method: "POST", json: { token } })
      : api<Portal>("/api/portal/me");
    request
      .then((portal) => { setView({ kind: "ready", portal }); })
      .catch((e: unknown) => {
        if (!token && e instanceof ApiError && e.status === 401) {
          setView({ kind: "no_link" });
          return;
        }
        const body = e instanceof ApiError ? (e.body as { error?: { can_request_new?: unknown } } | null) : null;
        setView({ kind: "problem", message: messageFor(e), token, canRequestNew: body?.error?.can_request_new === true });
      });
  }, []);

  return (
    <Frame>
      {view.kind === "loading" && <Box sx={{ display: "grid", placeItems: "center", py: 6 }}><CircularProgress aria-label="Loading" /></Box>}
      {view.kind === "ready" && <PortalHome portal={view.portal} onSignedOut={() => { setView({ kind: "signed_out" }); }} />}
      {view.kind === "problem" && (view.canRequestNew
        ? <NewLink token={view.token} message={view.message} />
        : <Alert severity="error">{view.message}</Alert>)}
      {view.kind === "no_link" && <Alert severity="info">Open the private link from your email to see this page.</Alert>}
      {view.kind === "signed_out" && <Alert severity="success">You have signed out on this device. To come back, ask us for a new link.</Alert>}
    </Frame>
  );
}
