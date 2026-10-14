import { useCallback, useEffect, useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import Chip from "@mui/material/Chip";
import FormControlLabel from "@mui/material/FormControlLabel";
import FormGroup from "@mui/material/FormGroup";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { api, messageFor } from "../api";
import { formatIST } from "../time";

type Status = "draft" | "queued" | "sent" | "delivered" | "failed" | "bounced";

export interface Update {
  id: number;
  status: Status;
  from_state: string;
  to_state: string;
  subject: string;
  to: string[];
  cc: string[];
  created_at: string;
  queued_at: string | null;
  sent_by: string | null;
  sent_at: string | null;
  delivered_at: string | null;
  failed_at: string | null;
}

interface Preview {
  subject: string;
  body: string;
  to: string[];
  cc: string[];
  other_contacts: { id: number; name: string; email: string }[];
}

const STATUS: Record<Status, { label: string; color: "default" | "info" | "success" | "error" | "warning" }> = {
  draft: { label: "Not sent", color: "warning" },
  queued: { label: "Sending", color: "info" },
  sent: { label: "Sent", color: "info" },
  delivered: { label: "Delivered", color: "success" },
  failed: { label: "Failed: call the client", color: "error" },
  bounced: { label: "Bounced: call the client", color: "error" },
};

const words = (state: string) => state.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

function Composer({ update, onSent, onCancel }: { update: Update; onSent: () => void; onCancel: () => void }) {
  const [note, setNote] = useState("");
  const [copies, setCopies] = useState<number[]>([]);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback((withNote: string, cc: number[]) => {
    api<Preview>(`/api/updates/${String(update.id)}/preview`, { method: "POST", json: { note: withNote, cc_contact_ids: cc } })
      .then(setPreview)
      .catch((e: unknown) => { setError(messageFor(e)); });
  }, [update.id]);
  useEffect(() => { refresh("", []); }, [refresh]);

  async function send() {
    setBusy(true);
    setError(null);
    try {
      await api(`/api/updates/${String(update.id)}/send`, { method: "POST", json: { note, cc_contact_ids: copies } });
      onSent();
    } catch (e) {
      setError(messageFor(e));
      setBusy(false);
    }
  }

  function toggle(id: number) {
    const next = copies.includes(id) ? copies.filter((c) => c !== id) : [...copies, id];
    setCopies(next);
    refresh(note, next);
  }

  return (
    <Paper variant="outlined" sx={{ p: 2, borderRadius: 3, mb: 2 }} aria-label="Update to send">
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <TextField
        label="Personal note (optional)"
        value={note}
        onChange={(e) => { setNote(e.target.value); }}
        onBlur={() => { refresh(note, copies); }}
        fullWidth
        multiline
        minRows={2}
        slotProps={{ htmlInput: { maxLength: 1000 } }}
        disabled={busy}
        sx={{ mb: 2 }}
      />
      {preview && preview.other_contacts.length > 0 && (
        <FormGroup sx={{ mb: 2 }}>
          <Typography variant="body2" color="text.secondary">Also send a copy to</Typography>
          {preview.other_contacts.map((c) => (
            <FormControlLabel key={c.id} control={<Checkbox checked={copies.includes(c.id)} onChange={() => { toggle(c.id); }} disabled={busy} />} label={`${c.name} (${c.email})`} />
          ))}
        </FormGroup>
      )}
      {preview && (
        <Box sx={{ mb: 2 }}>
          <Typography variant="body2" color="text.secondary">To {preview.to.join(", ")}{preview.cc.length > 0 ? `, copy to ${preview.cc.join(", ")}` : ""}</Typography>
          <Typography sx={{ fontWeight: 600, my: 1, overflowWrap: "anywhere" }}>{preview.subject}</Typography>
          <Box component="pre" data-testid="update-preview" sx={{ whiteSpace: "pre-wrap", fontFamily: "inherit", bgcolor: "action.hover", borderRadius: 2, p: 2, m: 0, overflowWrap: "anywhere" }}>{preview.body}</Box>
        </Box>
      )}
      <Stack direction="row" sx={{ gap: 1 }}>
        <Button variant="contained" onClick={() => void send()} disabled={busy || !preview}>Send update to client</Button>
        <Button onClick={onCancel} disabled={busy}>Cancel</Button>
      </Stack>
    </Paper>
  );
}

/** CR-03: the email each state change prepared. Nothing is sent until someone presses Send. */
export function ClientUpdates({ source }: { source: string }) {
  const [updates, setUpdates] = useState<Update[] | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [notice, setNotice] = useState<{ severity: "success" | "error"; text: string } | null>(null);

  const load = useCallback(() => {
    api<Update[]>(source)
      .then(setUpdates)
      .catch((e: unknown) => { setNotice({ severity: "error", text: messageFor(e) }); });
  }, [source]);
  useEffect(load, [load]);

  const waiting = updates?.filter((u) => u.status === "draft").length ?? 0;

  return (
    <Box component="section" aria-labelledby="updates-heading" sx={{ mb: 3 }}>
      <Stack direction="row" sx={{ alignItems: "center", gap: 1, mb: 1 }}>
        <Typography id="updates-heading" variant="h5" component="h2">Updates to the client</Typography>
        {waiting > 0 && <Chip size="small" color="warning" label={`${String(waiting)} to send`} data-testid="drafts-badge" />}
      </Stack>
      <Typography color="text.secondary" sx={{ mb: 2 }}>Each change prepares an email. It goes only when you send it.</Typography>
      {notice && <Alert severity={notice.severity} sx={{ mb: 2 }}>{notice.text}</Alert>}
      {updates?.length === 0 && <Typography color="text.secondary">No changes yet.</Typography>}
      {updates && updates.length > 0 && (
        <List aria-label="Updates to the client" disablePadding sx={{ border: 1, borderColor: "divider", borderRadius: 3 }}>
          {updates.map((u, i) => (
            <Box key={u.id}>
              <ListItem
                divider={i < updates.length - 1 && open !== u.id}
                secondaryAction={u.status === "draft" && open !== u.id && (
                  <Button size="small" variant="outlined" onClick={() => { setOpen(u.id); setNotice(null); }}>Preview and send</Button>
                )}
              >
                <ListItemText
                  primary={<Box component="span" sx={{ display: "flex", gap: 1, alignItems: "center", flexWrap: "wrap" }}><Chip size="small" variant="outlined" color={STATUS[u.status].color} label={STATUS[u.status].label} /><span>{words(u.from_state)} → {words(u.to_state)}</span></Box>}
                  secondary={u.status === "draft"
                    ? `Prepared ${formatIST(u.created_at)}`
                    : [`Sent by ${u.sent_by ?? "someone"}`, u.queued_at ? formatIST(u.queued_at) : null, u.cc.length > 0 ? `copy to ${u.cc.join(", ")}` : null].filter(Boolean).join(" · ")}
                  sx={{ pr: u.status === "draft" ? 18 : 0 }}
                />
              </ListItem>
              {open === u.id && (
                <Box sx={{ px: 2 }}>
                  <Composer
                    update={u}
                    onCancel={() => { setOpen(null); }}
                    onSent={() => { setOpen(null); setNotice({ severity: "success", text: "Sent to the client." }); load(); }}
                  />
                </Box>
              )}
            </Box>
          ))}
        </List>
      )}
    </Box>
  );
}
