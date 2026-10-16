import { useCallback, useEffect, useRef, useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import Chip from "@mui/material/Chip";
import FormControlLabel from "@mui/material/FormControlLabel";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { api, messageFor, uploadFile } from "../api";
import { formatIST } from "../time";

interface Item {
  id: number;
  label: string;
  required: boolean;
  status: "pending" | "checking" | "received";
  status_label: string;
  files: { version: number; filename: string; uploaded_at: string; checked: boolean }[];
}

export interface ChecklistData {
  items: Item[];
  still_needed: number;
  allowed: string;
  max_mb: number;
  can_confirm_no_activity?: boolean;
  no_activity_confirmed_at?: string | null;
  finished?: boolean;
  acknowledgement?: { reference: string; filename: string; filed_at: string } | null;
  messages?: { id: number; body: string; sent_at: string }[];
  can_message?: boolean;
}

/** FR-AL-07: write to the firm about this filing; kept with it, and the owner is told. */
function Messages({ data, csrf, onSent }: { data: ChecklistData; csrf: string; onSent: (d: ChecklistData) => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const messages = data.messages ?? [];
  if (!data.can_message && messages.length === 0) return null;

  async function send() {
    setBusy(true);
    setError(null);
    try {
      onSent(await api<ChecklistData>("/api/portal/messages", { method: "POST", json: { body: text.trim() }, headers: { "X-CSRF-Token": csrf } }));
      setText("");
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Box component="section" aria-labelledby="messages-heading" sx={{ mb: 3 }}>
      <Typography id="messages-heading" variant="h2" component="h2" sx={{ fontSize: "1.25rem", mb: 1 }}>Messages to us</Typography>
      {messages.length > 0 && (
        <Stack spacing={1} sx={{ mb: 2 }} aria-label="Your messages" role="list">
          {messages.map((m) => (
            <Paper key={m.id} role="listitem" variant="outlined" sx={{ p: 1.5, borderRadius: 2 }}>
              <Typography sx={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{m.body}</Typography>
              <Typography variant="body2" color="text.secondary">{formatIST(m.sent_at)}</Typography>
            </Paper>
          ))}
        </Stack>
      )}
      {data.can_message && (
        <>
          <TextField label="Your message" value={text} onChange={(e) => { setText(e.target.value); }} fullWidth multiline minRows={3} slotProps={{ htmlInput: { maxLength: 2000 } }} disabled={busy} />
          {error && <Alert severity="error" role="alert" sx={{ mt: 1 }}>{error}</Alert>}
          <Button variant="contained" sx={{ mt: 1 }} disabled={busy || !text.trim()} onClick={() => { void send(); }}>Send message</Button>
        </>
      )}
    </Box>
  );
}

/** FR-WF-06: the filing is done; the acknowledgement downloads straight from this session. */
function Completed({ ack }: { ack: NonNullable<ChecklistData["acknowledgement"]> }) {
  return (
    <Paper component="section" aria-label="Filing complete" variant="outlined" sx={{ p: 2, borderRadius: 3, mb: 3 }}>
      <Typography component="h2" sx={{ fontWeight: 600, fontSize: "1.25rem", mb: 1 }}>Your filing is complete</Typography>
      <Typography sx={{ mb: 1 }}>Filed on {formatIST(ack.filed_at)}. Acknowledgement reference {ack.reference}.</Typography>
      <Button variant="contained" component="a" href="/api/portal/acknowledgement" download={ack.filename}>Download the acknowledgement</Button>
    </Paper>
  );
}

const COLOR = { pending: "warning", checking: "info", received: "success" } as const;

function ItemCard({ item, busy, onFile, problem }: { item: Item; busy: boolean; onFile: (file: File) => void; problem: string | null }) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <Paper component="section" variant="outlined" sx={{ p: 2, borderRadius: 3 }} aria-label={item.label}>
      <Stack direction="row" sx={{ alignItems: "center", gap: 1, flexWrap: "wrap", mb: 1 }}>
        <Typography component="h3" sx={{ fontWeight: 600, mr: "auto", overflowWrap: "anywhere" }}>
          {item.label}{!item.required && <Typography component="span" color="text.secondary"> (if you have it)</Typography>}
        </Typography>
        <Chip size="small" color={COLOR[item.status]} label={item.status_label} />
      </Stack>
      {item.files.map((f) => (
        <Typography key={f.version} variant="body2" color="text.secondary" sx={{ overflowWrap: "anywhere" }}>
          {f.filename} · {formatIST(f.uploaded_at)}{f.checked ? "" : " · being checked"}
        </Typography>
      ))}
      {problem && <Alert severity="error" sx={{ mt: 1 }} role="alert">{problem}</Alert>}
      <input
        ref={input}
        type="file"
        hidden
        aria-label={`Upload ${item.label}`}
        accept=".pdf,.jpg,.jpeg,.png,.xlsx,.xls,.docx,.csv,.txt"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) onFile(file);
        }}
      />
      <Button sx={{ mt: 1.5 }} fullWidth variant={item.status === "pending" ? "contained" : "outlined"} disabled={busy} onClick={() => input.current?.click()}>
        {item.status === "pending" ? "Upload" : "Upload a newer copy"}
      </Button>
    </Paper>
  );
}

/** FR-CY-03: "nothing this period", said once, after the client ticks that it is true. */
function NoActivity({ csrf, onDone }: { csrf: string; onDone: (data: ChecklistData) => void }) {
  const [open, setOpen] = useState(false);
  const [sure, setSure] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      onDone(await api<ChecklistData>("/api/portal/no-activity", { method: "POST", json: { note: note.trim() || null }, headers: { "X-CSRF-Token": csrf } }));
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <Button variant="text" sx={{ mt: 2 }} onClick={() => { setOpen(true); }}>
        No activity this period? Tell us instead
      </Button>
    );
  }
  return (
    <Paper component="section" variant="outlined" aria-label="No activity this period" sx={{ p: 2, mt: 2, borderRadius: 3 }}>
      <Typography sx={{ mb: 1 }}>
        If there were no sales, purchases or other transactions in this period, tell us here and you need not upload anything. We will prepare a nil filing.
      </Typography>
      <TextField label="Anything we should know (optional)" value={note} onChange={(e) => { setNote(e.target.value); }} fullWidth multiline minRows={2} slotProps={{ htmlInput: { maxLength: 500 } }} sx={{ mb: 1 }} />
      <FormControlLabel control={<Checkbox checked={sure} onChange={(e) => { setSure(e.target.checked); }} />} label="I confirm there was no activity in this period" />
      {error && <Alert severity="error" role="alert" sx={{ my: 1 }}>{error}</Alert>}
      <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
        <Button variant="contained" disabled={!sure || busy} onClick={() => { void confirm(); }}>Confirm no activity</Button>
        <Button disabled={busy} onClick={() => { setOpen(false); }}>Cancel</Button>
      </Stack>
    </Paper>
  );
}

/** FR-AL-04: what is still needed, one upload button per item, and why a file was refused. */
export function Checklist({ csrf }: { csrf: string }) {
  const [data, setData] = useState<ChecklistData | null>(null);
  const [busyItem, setBusyItem] = useState<number | null>(null);
  const [problems, setProblems] = useState<Record<number, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const load = useCallback(() => {
    api<ChecklistData>("/api/portal/checklist")
      .then(setData)
      .catch((e: unknown) => { setError(messageFor(e)); });
  }, []);
  useEffect(load, [load]);

  async function send(item: Item, file: File) {
    setBusyItem(item.id);
    setDone(null);
    setProblems((p) => Object.fromEntries(Object.entries(p).filter(([id]) => id !== String(item.id))));
    try {
      setData(await uploadFile<ChecklistData>(`/api/uploads/portal/items/${String(item.id)}`, file, { "X-CSRF-Token": csrf }));
      setDone(`${file.name} received for ${item.label}. Thank you.`);
    } catch (e) {
      setProblems((p) => ({ ...p, [item.id]: messageFor(e) }));
    } finally {
      setBusyItem(null);
    }
  }

  if (error) return <Alert severity="error" sx={{ mb: 3 }}>{error}</Alert>;
  if (!data) return null;
  return (
    <>
      {main(data)}
      <Messages data={data} csrf={csrf} onSent={setData} />
    </>
  );

  function main(data: ChecklistData) {
    if (data.acknowledgement) return <Completed ack={data.acknowledgement} />;
    if (data.finished) return <Typography sx={{ mb: 3 }}>Your filing is done. Thank you.</Typography>;
    if (data.no_activity_confirmed_at) {
      return (
        <Alert severity="success" sx={{ mb: 3 }}>
          You told us there was no activity in this period ({formatIST(data.no_activity_confirmed_at)}). Thank you; nothing more is needed from you.
        </Alert>
      );
    }
    if (data.items.length === 0) return <Typography color="text.secondary" sx={{ mb: 3 }}>We do not need any documents from you for this filing.</Typography>;
    return (
      <Box component="section" aria-labelledby="checklist-heading" sx={{ mb: 3 }}>
        <Typography id="checklist-heading" variant="h2" component="h2" sx={{ fontSize: "1.25rem", mb: 1 }}>Documents we need</Typography>
        <Typography sx={{ mb: 1 }} data-testid="still-needed">
          {data.still_needed === 0 ? "We have everything we need. Thank you." : `${String(data.still_needed)} still needed.`}
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{data.allowed} files, up to {data.max_mb} MB each.</Typography>
        {done && <Alert severity="success" sx={{ mb: 2 }}>{done}</Alert>}
        <Stack spacing={2}>
          {data.items.map((item) => (
            <ItemCard key={item.id} item={item} busy={busyItem !== null} problem={problems[item.id] ?? null} onFile={(f) => void send(item, f)} />
          ))}
        </Stack>
        {data.can_confirm_no_activity && <NoActivity csrf={csrf} onDone={setData} />}
      </Box>
    );
  }
}
