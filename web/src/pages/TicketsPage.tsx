import { useEffect, useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import FormControlLabel from "@mui/material/FormControlLabel";
import List from "@mui/material/List";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemText from "@mui/material/ListItemText";
import MenuItem from "@mui/material/MenuItem";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { Link as RouterLink } from "react-router-dom";
import { api, messageFor } from "../api";
import { useUser } from "../auth/AuthProvider";
import { formatDay } from "../time";
import { STAGES } from "./TicketPage";

interface Person {
  id: number;
  full_name: string;
}

interface Row {
  code: string;
  type: "filing" | "task";
  title: string;
  client: { client_id: number; legal_name: string };
  owner: Person;
  assignee: Person | null;
  state: string;
  state_label: string;
  due_date: string | null;
}

interface Page {
  items: Row[];
  total: number;
  offset: number;
  limit: number;
}

const TASK_STATES: readonly (readonly [string, string])[] = [["open", "Open"], ["done", "Done"], ["cancelled", "Cancelled"]];
const PAGE = 50;

/** LL-051: every ticket this person may see, filings and tasks together, soonest due first. */
export function TicketsPage() {
  const me = useUser();
  const isAdmin = me.role === "administrator";
  const [people, setPeople] = useState<Person[]>([]);
  const [filters, setFilters] = useState({ type: "", state: "", owner: "", assignee: "", client: "", q: "", open: true });
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<Row[]>([]);
  const [page, setPage] = useState<Page | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Person[]>("/api/tickets/assignees").then(setPeople).catch(() => undefined);
  }, []);

  const params = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
  if (filters.type) params.set("type", filters.type);
  if (filters.state) params.set("state", filters.state);
  if (filters.owner) params.set("owner_id", filters.owner);
  if (filters.assignee) params.set("assignee_id", filters.assignee);
  if (/^\d{6}$/.test(filters.client.trim())) params.set("client_id", filters.client.trim());
  if (filters.q.trim()) params.set("q", filters.q.trim());
  if (filters.open) params.set("open_only", "true");
  const url = `/api/tickets?${params.toString()}`;

  useEffect(() => {
    let live = true;
    const timer = setTimeout(() => {
      api<Page>(url)
        .then((p) => {
          if (!live) return;
          setError(null);
          setPage(p);
          setRows((old) => (p.offset === 0 ? p.items : [...old, ...p.items]));
        })
        .catch((e: unknown) => { if (live) setError(messageFor(e)); });
    }, 250);
    return () => { live = false; clearTimeout(timer); };
  }, [url]);

  function set(key: keyof typeof filters, value: string | boolean) {
    setOffset(0);
    setFilters((f) => ({ ...f, [key]: value, ...(key === "type" ? { state: "" } : {}) }));
  }

  const stateOptions = filters.type === "task" ? TASK_STATES : filters.type === "filing" ? STAGES : [...STAGES, ...TASK_STATES];

  return (
    <>
      <Typography variant="h1" component="h1" sx={{ mb: 2, display: { xs: "none", md: "block" } }}>Work list</Typography>
      <Box sx={{ display: "grid", gap: 2, mb: 1, gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr", md: "repeat(3, 1fr)" } }}>
        <TextField label="Search" type="search" value={filters.q} onChange={(e) => { set("q", e.target.value); }} helperText="Ticket ID (TKT-…) or title" />
        <TextField label="Client ID" value={filters.client} onChange={(e) => { set("client", e.target.value); }} slotProps={{ htmlInput: { inputMode: "numeric", maxLength: 6 } }} helperText=" " />
        <TextField select label="Type" value={filters.type} onChange={(e) => { set("type", e.target.value); }} helperText=" ">
          <MenuItem value="">Filings and tasks</MenuItem>
          <MenuItem value="filing">Filings</MenuItem>
          <MenuItem value="task">Tasks</MenuItem>
        </TextField>
        <TextField select label="Stage or status" value={filters.state} onChange={(e) => { set("state", e.target.value); }}>
          <MenuItem value="">Any</MenuItem>
          {stateOptions.map(([key, label]) => <MenuItem key={key} value={key}>{label}</MenuItem>)}
        </TextField>
        <TextField select label="Assigned to" value={filters.assignee} onChange={(e) => { set("assignee", e.target.value); }}>
          <MenuItem value="">Anyone</MenuItem>
          {people.map((p) => <MenuItem key={p.id} value={String(p.id)}>{p.full_name}</MenuItem>)}
        </TextField>
        {isAdmin && (
          <TextField select label="Client owner" value={filters.owner} onChange={(e) => { set("owner", e.target.value); }}>
            <MenuItem value="">Anyone</MenuItem>
            {people.map((p) => <MenuItem key={p.id} value={String(p.id)}>{p.full_name}</MenuItem>)}
          </TextField>
        )}
      </Box>
      <FormControlLabel control={<Switch checked={filters.open} onChange={(e) => { set("open", e.target.checked); }} />} label="Only work still to do" sx={{ mb: 2 }} />
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {page === null && !error && <Typography color="text.secondary">Loading…</Typography>}
      {page && rows.length === 0 && <Typography color="text.secondary">Nothing here.</Typography>}
      {rows.length > 0 && (
        <>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>{page?.total} ticket{page?.total === 1 ? "" : "s"}</Typography>
          <List aria-label="Tickets" disablePadding sx={{ border: 1, borderColor: "divider", borderRadius: 3 }}>
            {rows.map((r, i) => (
              <ListItemButton key={r.code} component={RouterLink} to={`/tickets/${r.code}`} divider={i < rows.length - 1}>
                <ListItemText
                  primary={`${r.code} · ${r.title}`}
                  secondary={[
                    `${String(r.client.client_id)} ${r.client.legal_name}`,
                    r.state_label,
                    r.due_date ? `due ${formatDay(r.due_date)}` : null,
                    r.assignee ? `for ${r.assignee.full_name}` : null,
                  ].filter(Boolean).join(" · ")}
                  slotProps={{ primary: { sx: { fontWeight: 500 } } }}
                />
              </ListItemButton>
            ))}
          </List>
          {page && rows.length < page.total && (
            <Box sx={{ mt: 2, textAlign: "center" }}>
              <Button onClick={() => { setOffset(rows.length); }}>Show more</Button>
            </Box>
          )}
        </>
      )}
    </>
  );
}
