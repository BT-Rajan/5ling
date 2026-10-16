import { useCallback, useEffect, useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Typography from "@mui/material/Typography";
import { Link as RouterLink } from "react-router-dom";
import { api, messageFor } from "../api";
import { formatIST } from "../time";

type Kind = "documents_received" | "waiting_on_client" | "escalation" | "assigned" | "no_activity" | "phone_promise" | "client_message";

interface Note {
  id: number;
  kind: Kind;
  title: string;
  body: string;
  client_id: number;
  ticket: string | null;
  created_at: string;
  read: boolean;
  dismissed_at: string | null;
  dismissed_by: string | null;
  dismiss_note: string | null;
  needs_note: boolean;
}

const KIND: Record<Kind, { label: string; color: "success" | "warning" | "error" }> = {
  documents_received: { label: "Documents received", color: "success" },
  waiting_on_client: { label: "Waiting on client", color: "warning" },
  escalation: { label: "Escalation", color: "error" },
  assigned: { label: "Assigned to you", color: "success" },
  no_activity: { label: "No activity: review", color: "success" },
  phone_promise: { label: "Promised today", color: "warning" },
  client_message: { label: "Message from the client", color: "warning" },
};

function Card({ note, onChanged }: { note: Note; onChanged: () => void }) {
  const [asking, setAsking] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function post(path: string, json?: unknown) {
    setBusy(true);
    setError(null);
    try {
      await api(`/api/notifications/${String(note.id)}/${path}`, { method: "POST", ...(json !== undefined ? { json } : {}) });
      onChanged();
    } catch (e) {
      setError(messageFor(e));
      setBusy(false);
    }
  }

  return (
    <Paper component="article" variant="outlined" aria-label={note.title} sx={{ p: 2, borderRadius: 3, borderColor: note.kind === "escalation" && !note.dismissed_at ? "error.main" : "divider" }}>
      <Stack direction="row" sx={{ gap: 1, alignItems: "center", flexWrap: "wrap", mb: 1 }}>
        <Chip size="small" color={KIND[note.kind].color} label={KIND[note.kind].label} />
        {!note.read && !note.dismissed_at && <Chip size="small" variant="outlined" label="New" />}
        <Typography variant="body2" color="text.secondary" sx={{ ml: "auto" }}>{formatIST(note.created_at)}</Typography>
      </Stack>
      <Typography sx={{ fontWeight: 600, mb: 0.5, overflowWrap: "anywhere" }}>{note.title}</Typography>
      <Typography component="p" sx={{ whiteSpace: "pre-line", mb: 1, overflowWrap: "anywhere" }}>{note.body}</Typography>
      {note.dismissed_at && (
        <Typography variant="body2" color="text.secondary">
          Dismissed by {note.dismissed_by ?? "someone"}, {formatIST(note.dismissed_at)}{note.dismiss_note ? `: “${note.dismiss_note}”` : ""}
        </Typography>
      )}
      {error && <Alert severity="error" sx={{ my: 1 }}>{error}</Alert>}
      {!note.dismissed_at && (asking ? (
        <Box sx={{ mt: 1 }}>
          <TextField
            label={note.needs_note ? "What was done about it? (needed)" : "Note (optional)"}
            value={text}
            onChange={(e) => { setText(e.target.value); }}
            fullWidth
            multiline
            minRows={2}
            slotProps={{ htmlInput: { maxLength: 1000 } }}
            disabled={busy}
            autoFocus
          />
          <Stack direction="row" sx={{ gap: 1, mt: 1 }}>
            <Button variant="contained" disabled={busy || (note.needs_note && text.trim().length < 3)} onClick={() => void post("dismiss", { note: text.trim() || null })}>Dismiss</Button>
            <Button disabled={busy} onClick={() => { setAsking(false); }}>Cancel</Button>
          </Stack>
        </Box>
      ) : (
        <Stack direction="row" sx={{ gap: 1, mt: 1, flexWrap: "wrap" }}>
          {note.ticket && <Button size="small" component={RouterLink} to={`/tickets/${note.ticket}`} onClick={() => { if (!note.read) void post("read"); }}>Open {note.ticket}</Button>}
          {!note.read && <Button size="small" disabled={busy} onClick={() => void post("read")}>Mark read</Button>}
          <Button size="small" disabled={busy} onClick={() => { setAsking(true); }}>Dismiss</Button>
        </Stack>
      ))}
    </Paper>
  );
}

/** FR-IN-01 to FR-IN-03: what needs this person's attention. */
export function NotificationsPage() {
  const [show, setShow] = useState<"open" | "all">("open");
  const [notes, setNotes] = useState<Note[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<{ items: Note[]; unread: number }>(`/api/notifications?show=${show}`)
      .then((p) => { setNotes(p.items); })
      .catch((e: unknown) => { setError(messageFor(e)); });
  }, [show]);
  useEffect(load, [load]);

  return (
    <>
      <Typography variant="h1" component="h1" sx={{ mb: 2, display: { xs: "none", md: "block" } }}>Notifications</Typography>
      <ToggleButtonGroup size="small" exclusive value={show} onChange={(_, v: "open" | "all" | null) => { if (v) setShow(v); }} sx={{ mb: 2 }} aria-label="Show">
        <ToggleButton value="open">To deal with</ToggleButton>
        <ToggleButton value="all">All</ToggleButton>
      </ToggleButtonGroup>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {notes?.length === 0 && <Typography color="text.secondary">Nothing needs your attention.</Typography>}
      <Stack spacing={2} sx={{ maxWidth: 760 }}>
        {notes?.map((n) => <Card key={n.id} note={n} onChanged={load} />)}
      </Stack>
    </>
  );
}
