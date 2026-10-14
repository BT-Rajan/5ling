import { useCallback, useEffect, useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import Typography from "@mui/material/Typography";
import { api, messageFor } from "../api";
import { formatIST } from "../time";

type Status = "queued" | "sent" | "delivered" | "failed" | "bounced";

interface ClientEmail {
  id: number;
  kind_label: string;
  status: Status;
  subject: string;
  to: string[];
  queued_at: string | null;
  attempts: number;
  last_error: string | null;
  problem: "failed" | "delayed" | null;
  problem_label: string | null;
  followed_up_by: string | null;
  followed_up_at: string | null;
}

const STATUS: Record<Status, string> = { queued: "Sending", sent: "Sent", delivered: "Delivered", failed: "Failed", bounced: "Bounced" };

/** FR-AL-06: every email that went to the client about this ticket, and how it went. */
export function ClientEmails({ code, refresh = 0 }: { code: string; refresh?: number }) {
  const [emails, setEmails] = useState<ClientEmail[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api<ClientEmail[]>(`/api/tickets/${code}/emails`)
      .then(setEmails)
      .catch((e: unknown) => { setError(messageFor(e)); });
  }, [code]);
  useEffect(load, [load, refresh]);

  async function followedUp(email: ClientEmail) {
    setBusy(true);
    setError(null);
    try {
      await api(`/api/emails/${String(email.id)}/followed-up`, { method: "POST" });
      load();
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  if (!emails || emails.length === 0) return error ? <Alert severity="error" sx={{ mb: 3 }}>{error}</Alert> : null;
  return (
    <Box component="section" aria-labelledby="emails-heading" sx={{ mb: 3 }}>
      <Typography id="emails-heading" variant="h5" component="h2" sx={{ mb: 1 }}>Emails to the client</Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <List aria-label="Emails to the client" dense disablePadding sx={{ border: 1, borderColor: "divider", borderRadius: 3 }}>
        {emails.map((e, i) => (
          <ListItem
            key={e.id}
            divider={i < emails.length - 1}
            secondaryAction={e.problem && <Button size="small" disabled={busy} onClick={() => void followedUp(e)}>Mark followed up</Button>}
          >
            <ListItemText
              primary={<Box component="span" sx={{ display: "flex", gap: 1, alignItems: "center", flexWrap: "wrap" }}><span>{e.kind_label}: {e.subject}</span>{e.problem ? <Chip size="small" color={e.problem === "failed" ? "error" : "warning"} label={e.problem_label} /> : <Chip size="small" variant="outlined" label={STATUS[e.status]} />}</Box>}
              secondary={[
                e.queued_at ? formatIST(e.queued_at) : null,
                e.last_error && e.problem ? e.last_error : null,
                e.followed_up_by && e.followed_up_at ? `followed up by ${e.followed_up_by}, ${formatIST(e.followed_up_at)}` : null,
              ].filter(Boolean).join(" · ")}
              sx={{ pr: e.problem ? 18 : 0, overflowWrap: "anywhere" }}
            />
          </ListItem>
        ))}
      </List>
    </Box>
  );
}
