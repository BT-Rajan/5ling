import { useCallback, useEffect, useState, type SyntheticEvent } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { Link as RouterLink } from "react-router-dom";
import { api, messageFor } from "../api";
import { money } from "../catalog";
import type { FeeOverride } from "../components/Fees";
import { formatDay, formatIST } from "../time";

interface TaxRate {
  id: number;
  code: string;
  rate: string;
  effective_from: string;
  note: string | null;
  added_by: string | null;
  added_at: string;
  in_force: boolean;
}

/** FR-FE-01: client fees waiting for an Administrator to approve or reject. */
function Approvals() {
  const [rows, setRows] = useState<FeeOverride[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState<number | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api<FeeOverride[]>("/api/admin/fee-overrides").then(setRows).catch((e: unknown) => { setError(messageFor(e)); });
  }, []);
  useEffect(load, [load]);

  async function decide(o: FeeOverride, approve: boolean) {
    setBusy(true);
    setError(null);
    try {
      await api(`/api/admin/fee-overrides/${String(o.id)}/decision`, { method: "POST", json: approve ? { approve } : { approve, note: note.trim() } });
      setRejecting(null);
      setNote("");
      load();
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Box component="section" aria-labelledby="approvals-heading" sx={{ mb: 4 }}>
      <Typography id="approvals-heading" variant="h5" component="h2" sx={{ mb: 1 }}>Client fees to approve</Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {rows?.length === 0 && <Typography color="text.secondary">Nothing waiting.</Typography>}
      <Stack spacing={2}>
        {rows?.map((o) => (
          <Paper key={o.id} variant="outlined" sx={{ p: 2, borderRadius: 3 }} data-testid="fee-request">
            <Typography sx={{ fontWeight: 600 }}>
              <RouterLink to={`/clients/${String(o.client_id)}`}>{o.client_name} ({o.client_id})</RouterLink>: {money(o.original)} → {money(o.amount)}
            </Typography>
            <Typography variant="body2" color="text.secondary">Asked by {o.requested_by ?? "someone"}, {formatIST(o.requested_at)}</Typography>
            <Typography sx={{ mt: 1 }}>“{o.reason}”</Typography>
            {rejecting === o.id ? (
              <Stack spacing={1} sx={{ mt: 2 }}>
                <TextField label="Why is it rejected?" value={note} onChange={(e) => { setNote(e.target.value); }} slotProps={{ htmlInput: { maxLength: 500 } }} disabled={busy} autoFocus />
                <Stack direction="row" sx={{ gap: 1 }}>
                  <Button color="error" variant="contained" disabled={busy || !note.trim()} onClick={() => void decide(o, false)}>Reject</Button>
                  <Button disabled={busy} onClick={() => { setRejecting(null); }}>Cancel</Button>
                </Stack>
              </Stack>
            ) : (
              <Stack direction="row" sx={{ gap: 1, mt: 2 }}>
                <Button variant="contained" disabled={busy} onClick={() => void decide(o, true)}>Approve {money(o.amount)}</Button>
                <Button color="error" disabled={busy} onClick={() => { setRejecting(o.id); setNote(""); }}>Reject</Button>
              </Stack>
            )}
          </Paper>
        ))}
      </Stack>
    </Box>
  );
}

const RATE = /^\d{1,3}(\.\d{1,2})?$/;

/** FR-FE-09: GST rates, each from a date; a new one starts on a later date. */
function TaxRates() {
  const [rates, setRates] = useState<TaxRate[] | null>(null);
  const [rate, setRate] = useState("");
  const [from, setFrom] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<TaxRate[]>("/api/admin/tax-rates").then(setRates).catch((e: unknown) => { setError(messageFor(e)); });
  }, []);

  const valid = RATE.test(rate.trim()) && Number(rate) <= 100 && from !== "";

  async function add(event: SyntheticEvent) {
    event.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError(null);
    try {
      setRates(await api<TaxRate[]>("/api/admin/tax-rates", { method: "POST", json: { rate: rate.trim(), effective_from: from, note: note.trim() || null } }));
      setRate("");
      setFrom("");
      setNote("");
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Box component="section" aria-labelledby="tax-heading">
      <Typography id="tax-heading" variant="h5" component="h2" sx={{ mb: 1 }}>GST rate</Typography>
      <Typography color="text.secondary" sx={{ mb: 2 }}>Each invoice uses the rate in force on its date and keeps it. A new rate starts on a later date.</Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <List aria-label="GST rates" disablePadding sx={{ border: 1, borderColor: "divider", borderRadius: 3, mb: 2 }}>
        {rates?.map((r, i) => (
          <ListItem key={r.id} divider={i < rates.length - 1}>
            <ListItemText
              primary={`${r.rate}% from ${formatDay(r.effective_from)}${r.in_force ? " (in force)" : ""}`}
              secondary={[r.note, r.added_by ? `added by ${r.added_by}, ${formatIST(r.added_at)}` : null].filter(Boolean).join(" · ")}
            />
          </ListItem>
        ))}
      </List>
      <Paper component="form" onSubmit={(e) => void add(e)} noValidate variant="outlined" sx={{ p: 2, borderRadius: 3, display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" } }}>
        <TextField label="New rate (%)" value={rate} onChange={(e) => { setRate(e.target.value); }} slotProps={{ htmlInput: { inputMode: "decimal", maxLength: 6 } }} disabled={busy} />
        <TextField label="From" type="date" value={from} onChange={(e) => { setFrom(e.target.value); }} slotProps={{ inputLabel: { shrink: true } }} disabled={busy} />
        <TextField label="Note (optional)" value={note} onChange={(e) => { setNote(e.target.value); }} slotProps={{ htmlInput: { maxLength: 300 } }} disabled={busy} sx={{ gridColumn: { sm: "1 / -1" } }} />
        <Box><Button type="submit" variant="outlined" disabled={busy || !valid}>Add rate</Button></Box>
      </Paper>
    </Box>
  );
}

export function FeesSetupPage() {
  return (
    <>
      <Typography variant="h1" component="h1" sx={{ mb: 3, fontSize: { xs: "1.5rem", md: "2rem" } }}>Fees and tax</Typography>
      <Approvals />
      <TaxRates />
    </>
  );
}
