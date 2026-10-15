import { useEffect, useState, type SyntheticEvent } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import Paper from "@mui/material/Paper";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { Link as RouterLink } from "react-router-dom";
import { api, messageFor } from "../api";
import { formatIST } from "../time";

const FIELDS = [
  ["waiting_grace_days", "Days after the first alert before a cycle is waiting on the client", "days"],
  ["reminder_interval_days", "Days between reminders to clients", "days"],
  ["escalation_days_before", "Escalate a cycle with no documents this many days before it is due", "days"],
  ["portal_link_hours", "How long a client's private link lasts", "hours"],
  ["idle_timeout_minutes", "Sign Staff and Administrators out after this long without activity", "minutes"],
  ["break_glass_minutes", "How long a client stays open after an Administrator gives a reason", "minutes"],
] as const;

type Field = (typeof FIELDS)[number][0];
type Rules = Record<Field, number> & { limits: Record<Field, { default: number; min: number; max: number }>; updated_by: string | null; updated_at: string };

interface Grant {
  id: number;
  client_id: number;
  client_name: string;
  administrator: string;
  reason: string;
  granted_at: string;
  expires_at: string;
  active: boolean;
}

/** NFR-MN-01: the timings, as data an Administrator changes; FR-AC-05: who broke the glass. */
export function RulesPage() {
  const [rules, setRules] = useState<Rules | null>(null);
  const [form, setForm] = useState<Record<Field, string> | null>(null);
  const [grants, setGrants] = useState<Grant[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const take = (r: Rules) => {
    setRules(r);
    setForm(Object.fromEntries(FIELDS.map(([f]) => [f, String(r[f])])) as Record<Field, string>);
  };

  useEffect(() => {
    api<Rules>("/api/admin/rules").then(take).catch((e: unknown) => { setError(messageFor(e)); });
    api<Grant[]>("/api/admin/break-glass").then(setGrants).catch(() => undefined);
  }, []);

  const invalid = (f: Field): boolean => {
    if (!form || !rules) return false;
    const n = Number(form[f]);
    const { min, max } = rules.limits[f];
    return !/^\d+$/.test(form[f]) || n < min || n > max;
  };

  async function save(event: SyntheticEvent) {
    event.preventDefault();
    if (!form || FIELDS.some(([f]) => invalid(f))) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      take(await api<Rules>("/api/admin/rules", { method: "PUT", json: Object.fromEntries(FIELDS.map(([f]) => [f, Number(form[f])])) }));
      setSaved(true);
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Typography variant="h1" component="h1" sx={{ mb: 1, fontSize: { xs: "1.5rem", md: "2rem" } }}>Rules and access</Typography>
      <Typography color="text.secondary" sx={{ mb: 3 }}>Changes apply at once; no software release is needed.</Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {saved && <Alert severity="success" sx={{ mb: 2 }}>Saved.</Alert>}
      {rules && form && (
        <Paper component="form" aria-label="Rules" onSubmit={(e) => void save(e)} noValidate variant="outlined" sx={{ p: 2, borderRadius: 3, mb: 4 }}>
          <Box sx={{ display: "grid", gap: 2 }}>
            {FIELDS.map(([f, label, unit]) => (
              <TextField
                key={f}
                label={label}
                value={form[f]}
                onChange={(e) => { setForm({ ...form, [f]: e.target.value }); }}
                error={invalid(f)}
                helperText={`${String(rules.limits[f].min)} to ${String(rules.limits[f].max)} ${unit}; default ${String(rules.limits[f].default)}`}
                slotProps={{ htmlInput: { inputMode: "numeric", maxLength: 4 } }}
                disabled={busy}
              />
            ))}
          </Box>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>Last changed {rules.updated_by ? `by ${rules.updated_by}, ` : ""}{formatIST(rules.updated_at)}</Typography>
          <Button type="submit" variant="contained" sx={{ mt: 2 }} disabled={busy || FIELDS.some(([f]) => invalid(f))}>Save rules</Button>
        </Paper>
      )}
      <Typography variant="h5" component="h2" sx={{ mb: 1 }}>Clients opened by break-glass</Typography>
      {grants?.length === 0 && <Typography color="text.secondary">None yet.</Typography>}
      {grants && grants.length > 0 && (
        <List aria-label="Break-glass openings" disablePadding sx={{ border: 1, borderColor: "divider", borderRadius: 3 }}>
          {grants.map((g, i) => (
            <ListItem key={g.id} divider={i < grants.length - 1}>
              <ListItemText
                primary={<><RouterLink to={`/clients/${String(g.client_id)}`}>{g.client_name} ({g.client_id})</RouterLink> · {g.administrator}{g.active ? " · open now" : ""}</>}
                secondary={`“${g.reason}” · ${formatIST(g.granted_at)}`}
              />
            </ListItem>
          ))}
        </List>
      )}
    </>
  );
}
