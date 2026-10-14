import { useEffect, useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import ButtonBase from "@mui/material/ButtonBase";
import List from "@mui/material/List";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemText from "@mui/material/ListItemText";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { Link as RouterLink } from "react-router-dom";
import { api, messageFor } from "../api";
import { useUser } from "../auth/AuthProvider";
import { formatDay } from "../time";

type Bucket = "received" | "missing" | "due_this_week" | "overdue";

interface Row {
  code: string;
  client: { client_id: number; legal_name: string };
  filing: string;
  period_label: string;
  due_date: string;
  stage_label: string;
  missing: string[];
}

interface Worklist {
  on: string;
  week_from: string;
  week_to: string;
  owner: { id: number; full_name: string } | null;
  counts: Record<Bucket, number>;
  lists: Record<Bucket, { total: number; items: Row[] }>;
}

const BUCKETS: readonly { key: Bucket; label: string; empty: string }[] = [
  { key: "received", label: "Documents received", empty: "Nothing new has come in." },
  { key: "missing", label: "Waiting on client", empty: "No client is late with documents." },
  { key: "due_this_week", label: "Due this week", empty: "Nothing is due this week." },
  { key: "overdue", label: "Overdue", empty: "Nothing is overdue." },
];

/** FR-IN-04: the coordinator's day in four lists. */
export function DailyWorkList() {
  const user = useUser();
  const isAdmin = user.role === "administrator";
  const [owner, setOwner] = useState("");
  const [people, setPeople] = useState<{ id: number; full_name: string }[]>([]);
  const [data, setData] = useState<Worklist | null>(null);
  const [open, setOpen] = useState<Bucket>("received");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isAdmin) api<{ id: number; full_name: string }[]>("/api/tickets/assignees").then(setPeople).catch(() => undefined);
  }, [isAdmin]);

  useEffect(() => {
    let live = true;
    api<Worklist>(`/api/worklist${owner ? `?owner_id=${owner}` : ""}`)
      .then((w) => { if (live) { setData(w); setError(null); } })
      .catch((e: unknown) => { if (live) setError(messageFor(e)); });
    return () => { live = false; };
  }, [owner]);

  const chosen = BUCKETS.find((b) => b.key === open) ?? { key: open, label: "", empty: "" };
  const list = data?.lists[open];

  return (
    <Box component="section" aria-labelledby="today-heading" sx={{ mb: 4 }}>
      <Stack direction="row" sx={{ alignItems: "center", gap: 2, flexWrap: "wrap", mb: 2 }}>
        <Typography id="today-heading" variant="h1" component="h1" sx={{ mr: "auto", fontSize: { xs: "1.5rem", md: "2rem" } }}>
          Today{data ? `, ${formatDay(data.on)}` : ""}
        </Typography>
        {isAdmin && (
          <TextField select size="small" label="Whose clients" value={owner} onChange={(e) => { setOwner(e.target.value); }} sx={{ minWidth: 200 }}>
            <MenuItem value="">Everyone</MenuItem>
            {people.map((p) => <MenuItem key={p.id} value={String(p.id)}>{p.full_name}</MenuItem>)}
          </TextField>
        )}
      </Stack>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <Box role="tablist" aria-label="Work list" sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "1fr 1fr", sm: "repeat(4, 1fr)" }, mb: 2 }}>
        {BUCKETS.map((b) => (
          <ButtonBase
            key={b.key}
            role="tab"
            aria-selected={open === b.key}
            aria-controls="worklist-panel"
            onClick={() => { setOpen(b.key); }}
            sx={{ borderRadius: 3, textAlign: "left", display: "block" }}
          >
            <Paper
              variant="outlined"
              sx={{ p: 2, borderRadius: 3, borderWidth: open === b.key ? 2 : 1, borderColor: open === b.key ? "primary.main" : "divider" }}
            >
              <Typography sx={{ fontSize: "2rem", fontWeight: 700, lineHeight: 1.1 }} data-testid={`count-${b.key}`}>
                {data ? data.counts[b.key] : "–"}
              </Typography>
              <Typography variant="body2" color={b.key === "overdue" && (data?.counts.overdue ?? 0) > 0 ? "error" : "text.secondary"}>{b.label}</Typography>
            </Paper>
          </ButtonBase>
        ))}
      </Box>
      <Box id="worklist-panel" role="tabpanel" aria-label={chosen.label}>
        {list && list.items.length === 0 && <Typography color="text.secondary">{chosen.empty}</Typography>}
        {list && list.items.length > 0 && (
          <List aria-label={chosen.label} disablePadding sx={{ border: 1, borderColor: "divider", borderRadius: 3 }}>
            {list.items.map((r, i) => (
              <ListItemButton key={r.code} component={RouterLink} to={`/tickets/${r.code}`} divider={i < list.items.length - 1}>
                <ListItemText
                  primary={`${r.client.legal_name} · ${r.filing} ${r.period_label}`}
                  secondary={[
                    `${r.code} · ${r.stage_label} · due ${formatDay(r.due_date)}`,
                    r.missing.length > 0 ? `Missing: ${r.missing.join(", ")}` : null,
                  ].filter(Boolean).join(" · ")}
                />
              </ListItemButton>
            ))}
          </List>
        )}
        {list && list.total > list.items.length && <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>Showing the {list.items.length} due soonest of {list.total}.</Typography>}
      </Box>
    </Box>
  );
}
