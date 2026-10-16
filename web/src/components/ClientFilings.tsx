import { useCallback, useEffect, useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { Link as RouterLink } from "react-router-dom";
import { api, messageFor } from "../api";
import { formatIST } from "../time";

/** FR-PK-07 (C37): a client's own filings. Adding applies at once; a Staff removal waits. */

interface FilingRef { id: number; code: string; name: string }
export interface FilingChange {
  id: number;
  client_id: number;
  client_name: string;
  filing: FilingRef;
  kind: "add" | "remove";
  status: "pending" | "approved" | "rejected";
  reason: string;
  requested_by: string;
  requested_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
}
interface ClientFilingsData {
  filings: (FilingRef & { from_package: boolean; removal_pending: boolean })[];
  available: FilingRef[];
  changes: FilingChange[];
}

const STATUS = { pending: "waiting for an Administrator", approved: "done", rejected: "refused" } as const;

export function ClientFilings({ clientId, isAdmin }: { clientId: number; isAdmin: boolean }) {
  const [data, setData] = useState<ClientFilingsData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState("");
  const [removing, setRemoving] = useState<FilingRef | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api<ClientFilingsData>(`/api/clients/${String(clientId)}/filings`)
      .then(setData)
      .catch((e: unknown) => { setError(messageFor(e)); });
  }, [clientId]);
  useEffect(load, [load]);

  async function send(filingId: number, action: "add" | "remove") {
    setBusy(true);
    setError(null);
    try {
      setData(await api<ClientFilingsData>(`/api/clients/${String(clientId)}/filings`, { method: "POST", json: { filing_id: filingId, action, reason: reason.trim() } }));
      setAdding("");
      setRemoving(null);
      setReason("");
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  if (!data) return error ? <Alert severity="error" sx={{ mb: 3 }}>{error}</Alert> : null;
  const ready = reason.trim().length >= 5;
  return (
    <Paper component="section" aria-labelledby="client-filings-heading" variant="outlined" sx={{ p: 2, borderRadius: 3, mb: 3 }}>
      <Typography id="client-filings-heading" variant="h6" component="h2" sx={{ mb: 1 }}>Filings for this client</Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <List aria-label="This client's filings" disablePadding>
        {data.filings.map((f) => (
          <ListItem
            key={f.id}
            disableGutters
            secondaryAction={!f.removal_pending && <Button size="small" onClick={() => { setRemoving(f); setAdding(""); }}>Remove</Button>}
          >
            <ListItemText primary={`${f.code} · ${f.name}`} />
            <Stack direction="row" spacing={1} sx={{ mr: 10 }}>
              {!f.from_package && <Chip size="small" label="Added for this client" />}
              {f.removal_pending && <Chip size="small" color="warning" label="Removal waiting for approval" />}
            </Stack>
          </ListItem>
        ))}
      </List>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1} sx={{ mt: 2, alignItems: { sm: "flex-start" } }}>
        {data.available.length > 0 && (
          <TextField select label="Add a filing" value={adding} onChange={(e) => { setAdding(e.target.value); setRemoving(null); }} sx={{ minWidth: 220 }} disabled={busy}>
            {data.available.map((f) => <MenuItem key={f.id} value={String(f.id)}>{f.code} · {f.name}</MenuItem>)}
          </TextField>
        )}
        {(adding || removing) && (
          <>
            <TextField
              label={removing ? `Why remove ${removing.code}?` : "Why add it?"}
              value={reason}
              onChange={(e) => { setReason(e.target.value); }}
              helperText={removing && !isAdmin ? "An Administrator approves removals. The client keeps the filing until then." : "At least 5 characters."}
              slotProps={{ htmlInput: { maxLength: 500 } }}
              sx={{ flex: 1 }}
              disabled={busy}
            />
            <Button variant="contained" disabled={!ready || busy} onClick={() => { void send(removing ? removing.id : Number(adding), removing ? "remove" : "add"); }}>
              {removing ? (isAdmin ? "Remove" : "Ask to remove") : "Add"}
            </Button>
          </>
        )}
      </Stack>
      {data.changes.length > 0 && (
        <List aria-label="Filing changes" dense sx={{ mt: 2 }}>
          {data.changes.map((c) => (
            <ListItemText
              key={c.id}
              primary={`${c.kind === "add" ? "Added" : "Remove"} ${c.filing.code}: ${STATUS[c.status]}`}
              secondary={[`“${c.reason}”`, `${c.requested_by}, ${formatIST(c.requested_at)}`, c.decision_note ? `Note: ${c.decision_note}` : null].filter(Boolean).join(" · ")}
            />
          ))}
        </List>
      )}
    </Paper>
  );
}

/** For Administrators: removals waiting for a decision (FR-PK-07). */
export function FilingChangesQueue() {
  const [items, setItems] = useState<FilingChange[]>([]);
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const load = useCallback(() => {
    api<FilingChange[]>("/api/admin/filing-changes")
      .then(setItems)
      .catch((e: unknown) => { setError(messageFor(e)); });
  }, []);
  useEffect(load, [load]);

  async function decide(item: FilingChange, approve: boolean) {
    setError(null);
    try {
      const result = await api<FilingChange & { withdrawn: string[] }>(`/api/admin/filing-changes/${String(item.id)}/decision`, { method: "POST", json: { approve, note: notes[item.id]?.trim() || null } });
      setDone(approve ? `${item.filing.code} removed for ${item.client_name}; ${String(result.withdrawn.length)} unstarted cycles closed.` : `Removal of ${item.filing.code} refused.`);
      load();
    } catch (e) {
      setError(messageFor(e));
    }
  }

  if (items.length === 0 && !error && !done) return null;
  return (
    <Paper component="section" aria-labelledby="removals-heading" variant="outlined" sx={{ p: 2, borderRadius: 3, mb: 3 }}>
      <Typography id="removals-heading" variant="h6" component="h2" sx={{ mb: 1 }}>Filing removals waiting for you</Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {done && <Alert severity="success" sx={{ mb: 2 }}>{done}</Alert>}
      {items.map((item) => (
        <Box key={item.id} component="article" aria-label={`${item.client_name}: remove ${item.filing.code}`} sx={{ py: 1.5, borderTop: 1, borderColor: "divider" }}>
          <Typography>
            <RouterLink to={`/clients/${String(item.client_id)}`}>{item.client_name}</RouterLink>: remove {item.filing.code} · {item.filing.name}
          </Typography>
          <Typography variant="body2" color="text.secondary">“{item.reason}” · {item.requested_by}, {formatIST(item.requested_at)}</Typography>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1} sx={{ mt: 1 }}>
            <TextField size="small" label="Note (needed to refuse)" value={notes[item.id] ?? ""} onChange={(e) => { setNotes((n) => ({ ...n, [item.id]: e.target.value })); }} slotProps={{ htmlInput: { maxLength: 500 } }} sx={{ flex: 1 }} />
            <Button variant="contained" onClick={() => { void decide(item, true); }}>Approve</Button>
            <Button variant="outlined" disabled={!(notes[item.id] ?? "").trim()} onClick={() => { void decide(item, false); }}>Refuse</Button>
          </Stack>
        </Box>
      ))}
    </Paper>
  );
}
