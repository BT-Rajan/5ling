import { useCallback, useEffect, useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import List from "@mui/material/List";
import ListItemText from "@mui/material/ListItemText";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import MenuItem from "@mui/material/MenuItem";
import Typography from "@mui/material/Typography";
import { Link as RouterLink, useParams } from "react-router-dom";
import { api, messageFor } from "../api";
import { ClientLinks } from "../components/ClientLinks";
import { ClientUpdates } from "../components/ClientUpdates";
import { formatDay, formatIST } from "../time";

export type StageKey =
  | "scheduled"
  | "awaiting_documents"
  | "documents_received"
  | "assigned"
  | "preparing"
  | "in_review"
  | "ready_to_file"
  | "filed"
  | "acknowledged"
  | "closed";

/** FR-CY-02, in order. The server holds the same list and is the one that enforces it. */
export const STAGES: readonly (readonly [StageKey, string])[] = [
  ["scheduled", "Scheduled"],
  ["awaiting_documents", "Awaiting documents"],
  ["documents_received", "Documents received"],
  ["assigned", "Assigned"],
  ["preparing", "Preparing"],
  ["in_review", "In review"],
  ["ready_to_file", "Ready to file"],
  ["filed", "Filed"],
  ["acknowledged", "Acknowledged"],
  ["closed", "Closed"],
];
const LABEL = Object.fromEntries(STAGES) as Record<StageKey, string>;

interface Move {
  action: string;
  label: string;
  source: StageKey;
  target: StageKey;
  needs_note: boolean;
}

interface Ticket {
  code: string;
  type: "filing" | "task";
  title: string;
  client: { client_id: number; legal_name: string };
  created_at: string;
  cycle: {
    filing: { code: string; name: string };
    period_label: string;
    period_start: string;
    period_end: string;
    due_date: string;
    alert_date: string;
    stage: StageKey;
    stage_label: string;
    stage_changed_at: string;
    package_name: string;
    package_version: number;
    calendar_version: number;
    moves: Move[];
    extensions: { old_due_date: string; new_due_date: string; old_alert_date: string; new_alert_date: string; reason: string; applied_at: string; applied_by: string | null }[];
  } | null;
  task: {
    description: string | null;
    assignee: { id: number; full_name: string } | null;
    due_date: string | null;
    status: "open" | "done" | "cancelled";
    status_label: string;
    completed_at: string | null;
  } | null;
  history: { from_stage: StageKey | null; to_stage: StageKey; action: string; note: string | null; changed_by: string | null; changed_at: string }[];
  can_edit: boolean;
}

function TaskPanel({ ticket, onChanged }: { ticket: Ticket; onChanged: (t: Ticket) => void }) {
  const task = ticket.task;
  const [people, setPeople] = useState<{ id: number; full_name: string }[]>([]);
  const [title, setTitle] = useState(ticket.title);
  const [description, setDescription] = useState(task?.description ?? "");
  const [assignee, setAssignee] = useState(task?.assignee ? String(task.assignee.id) : "");
  const [due, setDue] = useState(task?.due_date ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (ticket.can_edit) api<{ id: number; full_name: string }[]>("/api/tickets/assignees").then(setPeople).catch(() => undefined);
  }, [ticket.can_edit]);

  if (!task) return null;

  async function save(body: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      onChanged(await api<Ticket>(`/api/tickets/${ticket.code}`, { method: "PATCH", json: body }));
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  const status = task.status;
  return (
    <Box sx={{ mb: 3 }}>
      <Box component="dl" sx={{ display: "grid", gridTemplateColumns: "max-content 1fr", columnGap: 3, rowGap: 0.75, mb: 2, "& dt": { color: "text.secondary" }, "& dd": { m: 0 } }}>
        <dt>Status</dt><dd data-testid="task-status">{task.status_label}{task.completed_at ? ` (${formatIST(task.completed_at)})` : ""}</dd>
        <dt>Assigned to</dt><dd>{task.assignee?.full_name ?? "Nobody yet"}</dd>
        <dt>Due</dt><dd>{task.due_date ? formatDay(task.due_date) : "No date"}</dd>
        {task.description && <><dt>Details</dt><dd style={{ whiteSpace: "pre-wrap" }}>{task.description}</dd></>}
      </Box>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <Stack direction="row" sx={{ gap: 1, flexWrap: "wrap", mb: 3 }}>
        {status === "open" && <Button variant="contained" disabled={busy} onClick={() => void save({ status: "done", expected_status: "open" })}>Mark done</Button>}
        {status === "open" && ticket.can_edit && <Button disabled={busy} onClick={() => void save({ status: "cancelled", expected_status: "open" })}>Cancel task</Button>}
        {status !== "open" && <Button variant="outlined" disabled={busy} onClick={() => void save({ status: "open", expected_status: status })}>Reopen</Button>}
      </Stack>
      {ticket.can_edit && (
        <Paper variant="outlined" sx={{ p: 2, borderRadius: 3 }}>
          <Typography variant="subtitle1" component="h3" sx={{ fontWeight: 600, mb: 2 }}>Edit task</Typography>
          <Stack spacing={2}>
            <TextField label="Title" value={title} onChange={(e) => { setTitle(e.target.value); }} disabled={busy} />
            <TextField label="Details" value={description} onChange={(e) => { setDescription(e.target.value); }} multiline minRows={2} disabled={busy} />
            <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" } }}>
              <TextField select label="Assigned to" value={assignee} onChange={(e) => { setAssignee(e.target.value); }} disabled={busy}>
                <MenuItem value="">Nobody yet</MenuItem>
                {people.map((p) => <MenuItem key={p.id} value={String(p.id)}>{p.full_name}</MenuItem>)}
              </TextField>
              <TextField label="Due" type="date" value={due} onChange={(e) => { setDue(e.target.value); }} slotProps={{ inputLabel: { shrink: true } }} disabled={busy} />
            </Box>
            <Box>
              <Button variant="outlined" disabled={busy || !title.trim()}
                onClick={() => void save({ title: title.trim(), description: description.trim() || null, assignee_id: assignee ? Number(assignee) : null, due_date: due || null })}>
                Save task
              </Button>
            </Box>
          </Stack>
        </Paper>
      )}
    </Box>
  );
}

function Timeline({ current }: { current: StageKey }) {
  const at = STAGES.findIndex(([key]) => key === current);
  return (
    <Box component="ol" aria-label="Stages" sx={{ listStyle: "none", p: 0, m: 0, mb: 3, display: "grid", gap: 0.75, gridTemplateColumns: { xs: "1fr 1fr", sm: "repeat(5, 1fr)" } }}>
      {STAGES.map(([key, label], i) => {
        const state = i < at ? "done" : i === at ? "current" : "upcoming";
        return (
          <Box
            component="li"
            key={key}
            aria-current={state === "current" ? "step" : undefined}
            sx={{
              px: 1.5,
              py: 1,
              borderRadius: 2,
              fontSize: "0.875rem",
              border: 1,
              borderColor: state === "upcoming" ? "divider" : "primary.main",
              bgcolor: state === "current" ? "primary.main" : state === "done" ? "primary.light" : "transparent",
              color: state === "current" ? "primary.contrastText" : state === "done" ? "primary.dark" : "text.secondary",
              fontWeight: state === "current" ? 700 : 500,
            }}
          >
            <span aria-hidden="true">{state === "done" ? "✓ " : `${String(i + 1)}. `}</span>
            {label}
            <Box component="span" sx={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)" }}>
              {state === "done" ? " (done)" : state === "current" ? " (now)" : ""}
            </Box>
          </Box>
        );
      })}
    </Box>
  );
}

function Actions({ ticket, onMoved }: { ticket: Ticket; onMoved: (t: Ticket) => void }) {
  const cycle = ticket.cycle;
  const [noteFor, setNoteFor] = useState<Move | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!cycle) return null;

  async function run(move: Move, withNote?: string) {
    if (!cycle) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api<Ticket>(`/api/tickets/${ticket.code}/transition`, {
        method: "POST",
        json: { action: move.action, expected_stage: cycle.stage, ...(withNote ? { note: withNote } : {}) },
      });
      setNoteFor(null);
      setNote("");
      onMoved(updated);
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  if (cycle.moves.length === 0) return <Typography color="text.secondary" sx={{ mb: 3 }}>This cycle is closed.</Typography>;
  return (
    <Box sx={{ mb: 3 }}>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {noteFor ? (
        <Paper variant="outlined" sx={{ p: 2, borderRadius: 3 }}>
          <TextField label={`Why? (${noteFor.label})`} value={note} onChange={(e) => { setNote(e.target.value); }} fullWidth multiline minRows={2} autoFocus disabled={busy} />
          <Stack direction="row" sx={{ mt: 2, gap: 1 }}>
            <Button variant="contained" disabled={busy || !note.trim()} onClick={() => void run(noteFor, note.trim())}>{noteFor.label}</Button>
            <Button disabled={busy} onClick={() => { setNoteFor(null); }}>Cancel</Button>
          </Stack>
        </Paper>
      ) : (
        <Stack direction="row" sx={{ gap: 1, flexWrap: "wrap" }}>
          {cycle.moves.map((m, i) => (
            <Button
              key={m.action}
              variant={i === 0 && !m.needs_note ? "contained" : "outlined"}
              disabled={busy}
              onClick={() => { if (m.needs_note) setNoteFor(m); else void run(m); }}
            >
              {m.label}
            </Button>
          ))}
        </Stack>
      )}
    </Box>
  );
}

export function TicketPage() {
  const { ticketId } = useParams();
  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [updatesRound, setUpdatesRound] = useState(0);
  // A move or a task change prepares a new client update: list it at once.
  const changed = (next: Ticket) => { setTicket(next); setUpdatesRound((r) => r + 1); };
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Ticket>(`/api/tickets/${encodeURIComponent(ticketId ?? "")}`)
      .then(setTicket)
      .catch((e: unknown) => { setError(messageFor(e)); });
  }, [ticketId]);
  useEffect(load, [load]);

  if (error) return <Alert severity="error">{error}</Alert>;
  if (!ticket) return <Typography color="text.secondary">Loading…</Typography>;
  const cycle = ticket.cycle;
  const originalDue = cycle?.extensions.at(-1)?.old_due_date;  // extensions are newest first
  return (
    <Box sx={{ maxWidth: 820 }}>
      {ticket.can_edit ? (
        <Button component={RouterLink} to={`/clients/${String(ticket.client.client_id)}`} size="small" sx={{ mb: 1, ml: -1 }}>
          ← {ticket.client.legal_name}
        </Button>
      ) : (
        <Button component={RouterLink} to="/work" size="small" sx={{ mb: 1, ml: -1 }}>← Work list</Button>
      )}
      <Typography variant="overline" color="text.secondary" component="p">{ticket.type === "filing" ? "Filing ticket" : "Task ticket"}</Typography>
      <Typography component="p" sx={{ fontSize: "1.75rem", fontWeight: 700, letterSpacing: "0.04em" }} data-testid="ticket-code">{ticket.code}</Typography>
      <Typography variant="h1" component="h1" sx={{ mb: 2, fontSize: { xs: "1.5rem", md: "2rem" } }}>{ticket.title}</Typography>
      {cycle && (
        <>
          <Box component="dl" sx={{ display: "grid", gridTemplateColumns: "max-content 1fr", columnGap: 3, rowGap: 0.75, mb: 3, "& dt": { color: "text.secondary" }, "& dd": { m: 0 } }}>
            <dt>Filing</dt><dd>{cycle.filing.code} · {cycle.filing.name}</dd>
            <dt>Period</dt><dd>{cycle.period_label} ({formatDay(cycle.period_start)} to {formatDay(cycle.period_end)})</dd>
            <dt>Due</dt><dd>{formatDay(cycle.due_date)}{originalDue && <> (extended from {formatDay(originalDue)})</>}</dd>
            <dt>Client alert</dt><dd>{formatDay(cycle.alert_date)}</dd>
            {cycle.extensions.map((x, i) => (
              <Box component="div" key={i} sx={{ display: "contents" }}>
                <dt>Extension</dt>
                <dd>{formatDay(x.old_due_date)} → {formatDay(x.new_due_date)}: “{x.reason}” ({x.applied_by ?? "System"}, {formatIST(x.applied_at)})</dd>
              </Box>
            ))}
            <dt>Built from</dt><dd>{cycle.package_name} version {cycle.package_version}, calendar version {cycle.calendar_version}</dd>
          </Box>
          <Typography variant="h5" component="h2" sx={{ mb: 1 }}>Stage: {cycle.stage_label}</Typography>
          <Timeline current={cycle.stage} />
          <Actions ticket={ticket} onMoved={changed} />
          {ticket.can_edit && <ClientLinks code={ticket.code} />}
        </>
      )}
      {ticket.can_edit && <ClientUpdates key={updatesRound} source={`/api/tickets/${ticket.code}/updates`} />}
      {ticket.task && <TaskPanel key={`${ticket.code}-${ticket.task.status}-${String(ticket.task.assignee?.id ?? 0)}`} ticket={ticket} onChanged={changed} />}
      <Typography variant="h5" component="h2" sx={{ mb: 1 }}>History</Typography>
      <List aria-label="Ticket history" disablePadding>
        {ticket.history.map((h, i) => (
          <ListItemText
            key={i}
            sx={{ py: 1 }}
            primary={h.from_stage ? `${LABEL[h.from_stage]} → ${LABEL[h.to_stage]}` : `Created as ${LABEL[h.to_stage]}`}
            secondary={[formatIST(h.changed_at), h.changed_by ?? "System", h.note ? `“${h.note}”` : null].filter(Boolean).join(" · ")}
          />
        ))}
      </List>
    </Box>
  );
}
