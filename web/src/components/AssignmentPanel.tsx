import { useCallback, useEffect, useState, type SyntheticEvent } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { api, messageFor } from "../api";
import { formatDay, formatIST } from "../time";

interface Person {
  id: number;
  full_name: string;
  role: "administrator" | "staff" | "consultant";
}

interface Assignment {
  id: number;
  assignee: Person;
  work_due_date: string | null;
  note: string | null;
  assigned_by: string;
  assigned_at: string;
  ended_at: string | null;
  end_reason: "withdrawn" | "reassigned" | "cycle_closed" | null;
  end_note: string | null;
}

const ENDED = { withdrawn: "Withdrawn", reassigned: "Given to someone else", cycle_closed: "Ended when the filing closed" } as const;
const ROLE = { administrator: "Administrator", staff: "Staff", consultant: "Consultant" } as const;

/** FR-WF-01, FR-WF-02: who has the work, giving it to someone, and taking it back. */
export function AssignmentPanel({ code, onChanged }: { code: string; onChanged: () => void }) {
  const [data, setData] = useState<{ current: Assignment | null; history: Assignment[] } | null>(null);
  const [people, setPeople] = useState<Person[]>([]);
  const [who, setWho] = useState("");
  const [due, setDue] = useState("");
  const [note, setNote] = useState("");
  const [withdrawing, setWithdrawing] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<{ current: Assignment | null; history: Assignment[] }>(`/api/tickets/${code}/assignment`)
      .then(setData)
      .catch((e: unknown) => { setError(messageFor(e)); });
  }, [code]);
  useEffect(load, [load]);
  useEffect(() => {
    api<Person[]>("/api/assignment-candidates").then(setPeople).catch(() => undefined);
  }, []);

  async function post(path: string, json: unknown) {
    setBusy(true);
    setError(null);
    try {
      setData(await api(`/api/tickets/${code}/${path}`, { method: "POST", json }));
      setWho("");
      setDue("");
      setNote("");
      setReason("");
      setWithdrawing(false);
      onChanged();
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  function assign(event: SyntheticEvent) {
    event.preventDefault();
    void post("assignment", { assignee_id: Number(who), work_due_date: due || null, note: note.trim() || null });
  }

  const current = data?.current ?? null;
  return (
    <Box component="section" aria-labelledby="assignment-heading" sx={{ mb: 3 }}>
      <Typography id="assignment-heading" variant="h5" component="h2" sx={{ mb: 1 }}>Assignment</Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {current ? (
        <Paper variant="outlined" sx={{ p: 2, borderRadius: 3, mb: 2 }} data-testid="current-assignment">
          <Typography sx={{ fontWeight: 600 }}>{current.assignee.full_name} ({ROLE[current.assignee.role]})</Typography>
          <Typography variant="body2" color="text.secondary">
            {current.work_due_date ? `Work due ${formatDay(current.work_due_date)} · ` : ""}assigned by {current.assigned_by}, {formatIST(current.assigned_at)}
          </Typography>
          {current.note && <Typography sx={{ mt: 1 }}>{current.note}</Typography>}
          {withdrawing ? (
            <Stack spacing={1} sx={{ mt: 2 }}>
              <TextField label="Why are you taking it back?" value={reason} onChange={(e) => { setReason(e.target.value); }} slotProps={{ htmlInput: { maxLength: 1000 } }} disabled={busy} autoFocus />
              <Stack direction="row" sx={{ gap: 1 }}>
                <Button color="error" variant="contained" disabled={busy || reason.trim().length < 3} onClick={() => void post("assignment/withdraw", { note: reason.trim() })}>Withdraw</Button>
                <Button disabled={busy} onClick={() => { setWithdrawing(false); }}>Cancel</Button>
              </Stack>
            </Stack>
          ) : (
            <Button size="small" color="error" sx={{ mt: 1, ml: -1 }} onClick={() => { setWithdrawing(true); }}>Withdraw the assignment</Button>
          )}
        </Paper>
      ) : (
        data && <Typography color="text.secondary" sx={{ mb: 2 }}>Not assigned to anyone.</Typography>
      )}
      <Box component="form" onSubmit={assign} noValidate sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: "2fr 1fr" }, mb: 2 }}>
        <TextField select label={current ? "Give it to someone else" : "Assign to"} value={who} onChange={(e) => { setWho(e.target.value); }} disabled={busy}>
          {people.filter((p) => p.id !== current?.assignee.id).map((p) => <MenuItem key={p.id} value={String(p.id)}>{p.full_name} · {ROLE[p.role]}</MenuItem>)}
        </TextField>
        <TextField label="Work due" type="date" value={due} onChange={(e) => { setDue(e.target.value); }} slotProps={{ inputLabel: { shrink: true } }} disabled={busy} />
        <TextField label="Note for them (optional)" value={note} onChange={(e) => { setNote(e.target.value); }} slotProps={{ htmlInput: { maxLength: 1000 } }} disabled={busy} sx={{ gridColumn: { sm: "1 / -1" } }} />
        <Box><Button type="submit" variant="outlined" disabled={busy || !who}>Assign</Button></Box>
      </Box>
      {data && data.history.some((a) => a.ended_at) && (
        <Typography variant="body2" color="text.secondary" component="div">
          Earlier: {data.history.filter((a) => a.ended_at).map((a) => `${a.assignee.full_name} (${a.end_reason ? ENDED[a.end_reason] : "ended"}${a.end_note ? `: “${a.end_note}”` : ""})`).join("; ")}
        </Typography>
      )}
    </Box>
  );
}
