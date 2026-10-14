import { useCallback, useEffect, useState, type SyntheticEvent } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Divider from "@mui/material/Divider";
import List from "@mui/material/List";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemText from "@mui/material/ListItemText";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { Link as RouterLink } from "react-router-dom";
import { api, messageFor } from "../api";
import { formatIST } from "../time";

type Status = "draft" | "queued" | "sent" | "delivered" | "failed" | "bounced";

interface EmailRow {
  id: number;
  kind: "test" | "audit_alert" | "client_update";
  status: Status;
  to: string[];
  subject: string;
  client: { client_id: number; legal_name: string } | null;
  ticket: string | null;
  attempts: number;
  last_error: string | null;
  created_at: string;
  queued_at: string | null;
  sent_at: string | null;
  delivered_at: string | null;
  failed_at: string | null;
}

interface EmailDetail extends EmailRow {
  cc: string[];
  body: string;
  next_attempt_at: string | null;
  events: { status: Status; source: "app" | "worker" | "provider"; detail: string | null; occurred_at: string }[];
}

interface Page {
  items: EmailRow[];
  total: number;
}

export const STATUS_LABEL: Record<Status, string> = {
  draft: "Draft",
  queued: "Queued",
  sent: "Sent",
  delivered: "Delivered",
  failed: "Failed",
  bounced: "Bounced",
};

const STATUS_COLOR: Record<Status, "default" | "info" | "success" | "error" | "warning"> = {
  draft: "default",
  queued: "info",
  sent: "info",
  delivered: "success",
  failed: "error",
  bounced: "error",
};

const KIND_LABEL: Record<EmailRow["kind"], string> = {
  test: "Test",
  audit_alert: "Audit alarm",
  client_update: "Client update",
};

const SOURCE_LABEL = { app: "Ledgerline", worker: "Send worker", provider: "Email provider" } as const;

function StatusChip({ status }: { status: Status }) {
  return <Chip size="small" label={STATUS_LABEL[status]} color={STATUS_COLOR[status]} variant="outlined" />;
}

function Detail({ email }: { email: EmailDetail }) {
  return (
    <Box component="section" aria-label="Email details" sx={{ border: 1, borderColor: "divider", borderRadius: 3, p: 2, mb: 3 }}>
      <Typography variant="h6" component="h3" sx={{ mb: 1, overflowWrap: "anywhere" }}>{email.subject}</Typography>
      <Box component="dl" sx={{ display: "grid", gridTemplateColumns: "max-content 1fr", columnGap: 2, rowGap: 0.5, mb: 2, "& dt": { color: "text.secondary" }, "& dd": { m: 0, overflowWrap: "anywhere" } }}>
        <dt>Status</dt><dd><StatusChip status={email.status} /></dd>
        <dt>To</dt><dd>{email.to.join(", ")}</dd>
        {email.cc.length > 0 && <><dt>Copy to</dt><dd>{email.cc.join(", ")}</dd></>}
        {email.ticket && <><dt>Ticket</dt><dd><RouterLink to={`/tickets/${email.ticket}`}>{email.ticket}</RouterLink></dd></>}
        {email.status === "queued" && email.next_attempt_at && <><dt>Next try</dt><dd>{formatIST(email.next_attempt_at)}</dd></>}
        {email.last_error && <><dt>Last error</dt><dd>{email.last_error}</dd></>}
      </Box>
      <Box component="pre" sx={{ whiteSpace: "pre-wrap", fontFamily: "inherit", bgcolor: "action.hover", borderRadius: 2, p: 2, m: 0, mb: 2, overflowWrap: "anywhere" }}>{email.body}</Box>
      <List aria-label="Email history" dense disablePadding>
        {email.events.map((e, i) => (
          <ListItemText
            key={i}
            primary={`${STATUS_LABEL[e.status]} · ${SOURCE_LABEL[e.source]}`}
            secondary={[formatIST(e.occurred_at), e.detail].filter(Boolean).join(" · ")}
          />
        ))}
      </List>
    </Box>
  );
}

function TestEmail({ onQueued }: { onQueued: (email: EmailDetail) => void }) {
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send(event: SyntheticEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const email = await api<EmailDetail>("/api/admin/emails/test", { method: "POST", json: to.trim() ? { to: to.trim() } : {} });
      setTo("");
      onQueued(email);
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Box component="form" onSubmit={(e) => void send(e)} noValidate aria-labelledby="test-heading" sx={{ mb: 3 }}>
      <Typography id="test-heading" variant="h5" component="h2" sx={{ mb: 1 }}>Send a test email</Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: "1fr auto" }, alignItems: "start" }}>
        <TextField label="Send to" type="email" value={to} onChange={(e) => { setTo(e.target.value); }} helperText="Leave empty to send it to yourself." disabled={busy} />
        <Button type="submit" variant="outlined" sx={{ height: 56 }} disabled={busy}>Send test email</Button>
      </Box>
    </Box>
  );
}

export function EmailPage() {
  const [status, setStatus] = useState("");
  const [page, setPage] = useState<Page | null>(null);
  const [open, setOpen] = useState<EmailDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Page>(`/api/admin/emails?limit=50&offset=0${status ? `&status=${status}` : ""}`)
      .then(setPage)
      .catch((e: unknown) => { setError(messageFor(e)); });
  }, [status]);
  useEffect(load, [load]);

  async function show(id: number) {
    setError(null);
    try {
      setOpen(await api<EmailDetail>(`/api/admin/emails/${String(id)}`));
    } catch (e) {
      setError(messageFor(e));
    }
  }

  return (
    <>
      <Button component={RouterLink} to="/setup" size="small" sx={{ mb: 1, ml: -1 }}>← Setup</Button>
      <Typography variant="h1" component="h1" sx={{ mb: 1, display: { xs: "none", md: "block" } }}>Email</Typography>
      <Typography color="text.secondary" sx={{ mb: 3, maxWidth: 720 }}>
        Every email Ledgerline sends, and how it went. Emails that cannot be sent are tried again for 24 hours before they are marked failed.
      </Typography>
      <Box sx={{ maxWidth: 960 }}>
        <TestEmail onQueued={(email) => { setOpen(email); load(); }} />
        <Divider sx={{ mb: 3 }} />
        {open && <Detail email={open} />}
        <Box sx={{ display: "flex", alignItems: "center", gap: 2, mb: 2, flexWrap: "wrap" }}>
          <Typography variant="h5" component="h2" sx={{ mr: "auto" }}>Outbox</Typography>
          <TextField select size="small" label="Status" value={status} onChange={(e) => { setStatus(e.target.value); }} sx={{ minWidth: 160 }}>
            <MenuItem value="">All sent</MenuItem>
            {(["queued", "sent", "delivered", "failed", "bounced"] as const).map((s) => <MenuItem key={s} value={s}>{STATUS_LABEL[s]}</MenuItem>)}
          </TextField>
        </Box>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        {page === null && !error && <Typography color="text.secondary">Loading…</Typography>}
        {page?.items.length === 0 && <Typography color="text.secondary">No emails.</Typography>}
        {page && page.items.length > 0 && (
          <List aria-label="Outbox" disablePadding sx={{ border: 1, borderColor: "divider", borderRadius: 3 }}>
            {page.items.map((e, i) => (
              <ListItemButton key={e.id} divider={i < page.items.length - 1} onClick={() => void show(e.id)} selected={open?.id === e.id}>
                <ListItemText
                  primary={<Box component="span" sx={{ display: "flex", gap: 1, alignItems: "center", flexWrap: "wrap" }}><StatusChip status={e.status} /><span>{e.subject}</span></Box>}
                  secondary={[KIND_LABEL[e.kind], e.client ? `${String(e.client.client_id)} · ${e.client.legal_name}` : e.to.join(", "), formatIST(e.queued_at ?? e.created_at), e.status === "queued" && e.attempts > 0 ? `tried ${String(e.attempts)}×` : null].filter(Boolean).join(" · ")}
                  slotProps={{ secondary: { sx: { overflowWrap: "anywhere" } } }}
                />
              </ListItemButton>
            ))}
          </List>
        )}
        {page && page.total > page.items.length && <Typography color="text.secondary" sx={{ mt: 1 }}>Showing the newest {page.items.length} of {page.total}.</Typography>}
      </Box>
    </>
  );
}
