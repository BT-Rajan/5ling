import { useCallback, useEffect, useState, type SyntheticEvent } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Divider from "@mui/material/Divider";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import MenuItem from "@mui/material/MenuItem";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { Link as RouterLink } from "react-router-dom";
import { ApiError, api, messageFor } from "../api";
import type { FilingType } from "../catalog";
import { formatDay, formatIST } from "../time";

interface Period {
  period_start: string;
  period_label: string;
  open_cycles: number;
  earliest_due: string;
  latest_due: string;
}

interface Affected {
  code: string;
  client_id: number;
  client_name: string;
  stage_label: string;
  old_due_date: string;
  new_due_date: string;
  old_alert_date: string;
  new_alert_date: string;
}

interface Preview {
  filing_code: string;
  period_label: string;
  new_due_date: string;
  changes: number;
  unchanged: number;
  rows: Affected[];
}

interface Extension {
  id: number;
  filing_code: string;
  filing_name: string;
  period_label: string;
  new_due_date: string;
  reason: string;
  cycles_changed: number;
  created_by: string | null;
  created_at: string;
}

type Notice = { severity: "success" | "error"; text: string };

const plural = (n: number, one: string, many: string) => `${String(n)} ${n === 1 ? one : many}`;

function AffectedTable({ preview }: { preview: Preview }) {
  if (preview.rows.length === 0) return null;
  return (
    <TableContainer sx={{ border: 1, borderColor: "divider", borderRadius: 3, mb: 2, maxHeight: 420 }}>
      <Table size="small" stickyHeader aria-label="Cycles that will move">
        <TableHead>
          <TableRow>
            <TableCell>Ticket</TableCell>
            <TableCell>Client</TableCell>
            <TableCell>Stage</TableCell>
            <TableCell>Due</TableCell>
            <TableCell>Client alert</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {preview.rows.map((r) => (
            <TableRow key={r.code}>
              <TableCell><RouterLink to={`/tickets/${r.code}`}>{r.code}</RouterLink></TableCell>
              <TableCell>{r.client_id} · {r.client_name}</TableCell>
              <TableCell>{r.stage_label}</TableCell>
              <TableCell>{formatDay(r.old_due_date)} → {formatDay(r.new_due_date)}</TableCell>
              <TableCell>{formatDay(r.old_alert_date)} → {formatDay(r.new_alert_date)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

function ExtensionForm({ onApplied }: { onApplied: () => void }) {
  const [filings, setFilings] = useState<FilingType[]>([]);
  const [filingId, setFilingId] = useState("");
  const [periods, setPeriods] = useState<Period[] | null>(null);
  const [periodStart, setPeriodStart] = useState("");
  const [newDue, setNewDue] = useState("");
  const [reason, setReason] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<FilingType[]>("/api/admin/filings")
      .then(setFilings)
      .catch((e: unknown) => { setNotice({ severity: "error", text: messageFor(e) }); });
  }, []);

  useEffect(() => {
    if (!filingId) return;
    api<Period[]>(`/api/admin/extensions/periods?filing_id=${encodeURIComponent(filingId)}`)
      .then(setPeriods)
      .catch((e: unknown) => { setNotice({ severity: "error", text: messageFor(e) }); });
  }, [filingId]);

  // Any change to what is asked for makes the preview out of date.
  function chooseFiling(id: string) {
    setFilingId(id);
    setPeriods(null);
    setPeriodStart("");
    setPreview(null);
  }

  const asked = { filing_id: Number(filingId), period_start: periodStart, new_due_date: newDue };
  const period = periods?.find((p) => p.period_start === periodStart);

  async function showPreview(event: SyntheticEvent) {
    event.preventDefault();
    setBusy(true);
    setNotice(null);
    try {
      setPreview(await api<Preview>("/api/admin/extensions/preview", { method: "POST", json: asked }));
    } catch (e) {
      setNotice({ severity: "error", text: messageFor(e) });
    } finally {
      setBusy(false);
    }
  }

  async function applyIt() {
    if (!preview) return;
    setBusy(true);
    setNotice(null);
    try {
      const done = await api<Extension>("/api/admin/extensions", {
        method: "POST",
        json: { ...asked, reason: reason.trim(), expected_changes: preview.changes },
      });
      setNotice({ severity: "success", text: `${done.filing_code} ${done.period_label} is now due ${formatDay(done.new_due_date)}. ${plural(done.cycles_changed, "cycle", "cycles")} moved.` });
      setPreview(null);
      setReason("");
      setNewDue("");
      onApplied();
    } catch (e) {
      if (e instanceof ApiError && e.code === "preview_stale") setPreview(null);
      setNotice({ severity: "error", text: messageFor(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Box component="section" aria-labelledby="apply-heading" sx={{ mb: 4, maxWidth: 960 }}>
      <Typography id="apply-heading" variant="h5" component="h2" sx={{ mb: 1 }}>Apply an extension</Typography>
      <Typography color="text.secondary" sx={{ mb: 2 }}>
        When the government extends a due date, move every open cycle for that filing and period at once. Filed and closed cycles keep their dates. Client alerts are counted back from the new date, and clients added later get the new date too.
      </Typography>
      {notice && <Alert severity={notice.severity} sx={{ mb: 2 }}>{notice.text}</Alert>}
      <Box component="form" onSubmit={(e) => void showPreview(e)} noValidate sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr 1fr auto" }, alignItems: "start", mb: 2 }}>
        <TextField select label="Filing" value={filingId} onChange={(e) => { chooseFiling(e.target.value); }} disabled={busy}>
          {filings.map((f) => <MenuItem key={f.id} value={String(f.id)}>{f.code} · {f.name}</MenuItem>)}
        </TextField>
        <TextField
          select
          label="Period"
          value={periodStart}
          onChange={(e) => { setPeriodStart(e.target.value); setPreview(null); }}
          disabled={busy || !periods || periods.length === 0}
          helperText={periods?.length === 0 ? "No open cycles for this filing." : period ? `${plural(period.open_cycles, "open cycle", "open cycles")}, due ${formatDay(period.earliest_due)}${period.latest_due !== period.earliest_due ? ` to ${formatDay(period.latest_due)}` : ""}` : " "}
        >
          {(periods ?? []).map((p) => <MenuItem key={p.period_start} value={p.period_start}>{p.period_label}</MenuItem>)}
        </TextField>
        <TextField label="New due date" type="date" value={newDue} onChange={(e) => { setNewDue(e.target.value); setPreview(null); }} slotProps={{ inputLabel: { shrink: true } }} disabled={busy} />
        <Button type="submit" variant="outlined" sx={{ height: 56 }} disabled={busy || !filingId || !periodStart || !newDue}>Preview</Button>
      </Box>
      {preview && (
        <Box aria-live="polite">
          <Typography sx={{ mb: 1 }} data-testid="extension-summary">
            {preview.changes === 0
              ? `No open cycle for ${preview.filing_code} ${preview.period_label} is due before ${formatDay(preview.new_due_date)}.`
              : `${plural(preview.changes, "cycle", "cycles")} of ${preview.filing_code} ${preview.period_label} will move to ${formatDay(preview.new_due_date)}.`}
            {preview.unchanged > 0 && ` ${plural(preview.unchanged, "open cycle is", "open cycles are")} already due on or after it and will not change.`}
          </Typography>
          {preview.rows.length < preview.changes && <Typography color="text.secondary" sx={{ mb: 1 }}>Showing the first {preview.rows.length}.</Typography>}
          <AffectedTable preview={preview} />
          {preview.changes > 0 && (
            <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: "1fr auto" }, alignItems: "start" }}>
              <TextField
                label="Reason"
                value={reason}
                onChange={(e) => { setReason(e.target.value); }}
                helperText="For example, the notification number. Kept with the change."
                slotProps={{ htmlInput: { maxLength: 500 } }}
                disabled={busy}
              />
              <Button variant="contained" sx={{ height: 56 }} onClick={() => void applyIt()} disabled={busy || reason.trim().length < 3}>
                Apply to {plural(preview.changes, "cycle", "cycles")}
              </Button>
            </Box>
          )}
        </Box>
      )}
    </Box>
  );
}

function ExtensionHistory({ items }: { items: Extension[] | null }) {
  return (
    <Box component="section" aria-labelledby="history-heading" sx={{ maxWidth: 960 }}>
      <Typography id="history-heading" variant="h5" component="h2" sx={{ mb: 1 }}>Extensions applied</Typography>
      {items === null && <Typography color="text.secondary">Loading…</Typography>}
      {items?.length === 0 && <Typography color="text.secondary">None yet.</Typography>}
      {items && items.length > 0 && (
        <List aria-label="Extensions applied" disablePadding sx={{ border: 1, borderColor: "divider", borderRadius: 3 }}>
          {items.map((x, i) => (
            <ListItem key={x.id} divider={i < items.length - 1}>
              <ListItemText
                primary={`${x.filing_code} ${x.period_label}: due ${formatDay(x.new_due_date)} (${plural(x.cycles_changed, "cycle", "cycles")})`}
                secondary={`“${x.reason}” · ${x.created_by ?? "Unknown"} · ${formatIST(x.created_at)}`}
              />
            </ListItem>
          ))}
        </List>
      )}
    </Box>
  );
}

export function ExtensionsPage() {
  const [history, setHistory] = useState<Extension[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api<Extension[]>("/api/admin/extensions")
      .then(setHistory)
      .catch((e: unknown) => { setError(messageFor(e)); });
  }, []);
  useEffect(load, [load]);

  return (
    <>
      <Button component={RouterLink} to="/setup" size="small" sx={{ mb: 1, ml: -1 }}>← Setup</Button>
      <Typography variant="h1" component="h1" sx={{ mb: 3, display: { xs: "none", md: "block" } }}>Due-date extensions</Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <ExtensionForm onApplied={load} />
      <Divider sx={{ mb: 3 }} />
      <ExtensionHistory items={history} />
    </>
  );
}
