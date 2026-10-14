import { useCallback, useEffect, useState } from "react";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { api, download, messageFor, saveFile } from "../api";
import { eventLabel, triggerLabel, type AuditCheck, type AuditEntry, type AuditPage as Page } from "../audit";
import { formatIST } from "../time";

interface Person {
  id: number;
  full_name: string;
}

interface Filters {
  event: string;
  actor: string;
  from: string;
  to: string;
}

const NO_FILTERS: Filters = { event: "", actor: "", from: "", to: "" };
const PAGE_SIZE = 50;

/** Only the filters that are set, named as the server expects. */
function filterBody(f: Filters): Record<string, string | number> {
  const body: Record<string, string | number> = {};
  if (f.event) body.event = f.event;
  if (f.actor) body.actor_user_id = Number(f.actor);
  if (f.from) body.from_date = f.from;
  if (f.to) body.to_date = f.to;
  return body;
}

function query(f: Filters, beforeSeq: number | null): string {
  const params = new URLSearchParams({ limit: String(PAGE_SIZE) });
  for (const [k, v] of Object.entries(filterBody(f))) params.set(k, String(v));
  if (beforeSeq !== null) params.set("before_seq", String(beforeSeq));
  return `/api/admin/audit/entries?${params.toString()}`;
}

function who(e: AuditEntry): string {
  if (e.actor_name) return e.actor_name;
  if (e.actor_user_id !== null) return `Person #${e.actor_user_id}`;
  return "Not signed in";
}

function describe(e: AuditEntry): string {
  const parts = [formatIST(e.occurred_at), who(e)];
  if (e.target_type) parts.push(`${e.target_type} ${e.target_id ?? ""}`.trim());
  if (e.ip) parts.push(e.ip);
  const details = Object.entries(e.details)
    .filter(([, v]) => v !== null && v !== "")
    .map(([k, v]) => `${k.replaceAll("_", " ")}: ${Array.isArray(v) ? v.join(", ") : String(v)}`);
  return [...parts, ...details].join(" · ");
}

function ChainStatus({ check, busy, onCheck }: { check: AuditCheck | null | undefined; busy: boolean; onCheck: () => void }) {
  const button = (
    <Button color="inherit" onClick={onCheck} disabled={busy}>
      {busy ? "Checking…" : "Check now"}
    </Button>
  );
  if (check === undefined) return <Typography color="text.secondary" sx={{ mb: 3 }}>Loading…</Typography>;
  if (check === null) {
    return (
      <Alert severity="info" action={button} sx={{ mb: 3 }}>
        The log has not been checked yet. The daily check runs at 02:15 India time.
      </Alert>
    );
  }
  if (check.ok) {
    return (
      <Alert severity="success" action={button} sx={{ mb: 3 }} data-testid="chain-status">
        Intact. {check.entries_checked} entries checked on {formatIST(check.finished_at)} ({triggerLabel(check.trigger)}).
      </Alert>
    );
  }
  return (
    <Alert severity="error" action={button} sx={{ mb: 3 }} data-testid="chain-status">
      <AlertTitle>The audit log may have been tampered with</AlertTitle>
      The {triggerLabel(check.trigger)} on {formatIST(check.finished_at)} failed
      {check.first_bad_seq ? ` at entry ${check.first_bad_seq}` : ""}: {check.problem_text ?? check.problem}. Entries
      before that point checked out. Keep an export, and investigate before relying on later entries.
    </Alert>
  );
}

export function AuditPage() {
  const [check, setCheck] = useState<AuditCheck | null | undefined>(undefined);
  const [checking, setChecking] = useState(false);
  const [events, setEvents] = useState<string[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [items, setItems] = useState<AuditEntry[] | null>(null);
  const [next, setNext] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    api<{ latest: AuditCheck | null }>("/api/admin/audit/status")
      .then(({ latest }) => { setCheck(latest); })
      .catch((e: unknown) => { setError(messageFor(e)); });
    api<string[]>("/api/admin/audit/events").then(setEvents).catch(() => undefined);
    api<Person[]>("/api/admin/users").then(setPeople).catch(() => undefined);
  }, []);

  const load = useCallback((f: Filters, beforeSeq: number | null) => {
    api<Page>(query(f, beforeSeq))
      .then((page) => {
        setError(null);
        setItems((old) => (beforeSeq === null || old === null ? page.items : [...old, ...page.items]));
        setNext(page.next_before_seq);
      })
      .catch((e: unknown) => { setError(messageFor(e)); });
  }, []);

  useEffect(() => {
    load(filters, null);
  }, [filters, load]);

  function changeFilters(update: (f: Filters) => Filters) {
    setItems(null); // show "Loading…" rather than results for the old filters
    setFilters(update);
  }

  function set<K extends keyof Filters>(key: K, value: Filters[K]) {
    changeFilters((f) => ({ ...f, [key]: value }));
  }

  async function checkNow() {
    setChecking(true);
    try {
      setCheck(await api<AuditCheck>("/api/admin/audit/verify", { method: "POST", timeoutMs: 120_000 }));
      load(filters, null); // the check is itself on the record
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setChecking(false);
    }
  }

  async function exportCsv() {
    setExporting(true);
    setError(null);
    try {
      const file = await download("/api/admin/audit/export", filterBody(filters));
      saveFile(file);
      api<{ latest: AuditCheck | null }>("/api/admin/audit/status")
        .then(({ latest }) => { setCheck(latest); })
        .catch(() => undefined);
      load(filters, null);
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setExporting(false);
    }
  }

  const filtered = Object.values(filters).some(Boolean);

  return (
    <>
      <Stack direction="row" sx={{ alignItems: "center", justifyContent: "space-between", mb: 2 }}>
        <Typography variant="h1" component="h1" sx={{ display: { xs: "none", md: "block" } }}>Audit log</Typography>
        <Button variant="contained" onClick={() => void exportCsv()} disabled={exporting} sx={{ ml: "auto" }}>
          {exporting ? "Exporting…" : "Export CSV"}
        </Button>
      </Stack>

      <ChainStatus check={check} busy={checking} onCheck={() => void checkNow()} />

      <Box
        component="fieldset"
        sx={{ border: 0, p: 0, m: 0, mb: 2, display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr", md: "2fr 2fr 1fr 1fr" } }}
      >
        <Box component="legend" sx={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)" }}>
          Filter the log
        </Box>
        <TextField select label="What happened" value={filters.event} onChange={(e) => { set("event", e.target.value); }} fullWidth>
          <MenuItem value="">Everything</MenuItem>
          {events.map((ev) => (
            <MenuItem key={ev} value={ev}>{eventLabel(ev)}</MenuItem>
          ))}
        </TextField>
        <TextField select label="Who" value={filters.actor} onChange={(e) => { set("actor", e.target.value); }} fullWidth>
          <MenuItem value="">Anyone</MenuItem>
          {people.map((p) => (
            <MenuItem key={p.id} value={String(p.id)}>{p.full_name}</MenuItem>
          ))}
        </TextField>
        <TextField label="From" type="date" value={filters.from} onChange={(e) => { set("from", e.target.value); }} slotProps={{ inputLabel: { shrink: true } }} fullWidth />
        <TextField label="To" type="date" value={filters.to} onChange={(e) => { set("to", e.target.value); }} slotProps={{ inputLabel: { shrink: true } }} fullWidth />
      </Box>
      {filtered && (
        <Button size="small" onClick={() => { changeFilters(() => NO_FILTERS); }} sx={{ mb: 2 }}>Clear filters</Button>
      )}

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {items === null && !error && <Typography color="text.secondary">Loading…</Typography>}
      {items?.length === 0 && <Typography color="text.secondary">Nothing matches these filters.</Typography>}
      {items && items.length > 0 && (
        <List disablePadding aria-label="Audit entries" sx={{ border: 1, borderColor: "divider", borderRadius: 3 }}>
          {items.map((e, i) => (
            <ListItem key={e.seq} divider={i < items.length - 1} sx={{ py: 1.25 }}>
              <ListItemText
                primary={`#${e.seq} · ${eventLabel(e.event)}`}
                secondary={describe(e)}
                slotProps={{
                  primary: { sx: { fontWeight: 500, color: e.event === "audit.check_failed" ? "error.main" : undefined } },
                  secondary: { sx: { overflowWrap: "anywhere" } },
                }}
              />
            </ListItem>
          ))}
        </List>
      )}
      {next !== null && (
        <Box sx={{ mt: 2, textAlign: "center" }}>
          <Button onClick={() => { load(filters, next); }}>Show older entries</Button>
        </Box>
      )}
    </>
  );
}
