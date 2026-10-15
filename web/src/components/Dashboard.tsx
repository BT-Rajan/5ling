import { useEffect, useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import ButtonBase from "@mui/material/ButtonBase";
import List from "@mui/material/List";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemText from "@mui/material/ListItemText";
import Paper from "@mui/material/Paper";
import Typography from "@mui/material/Typography";
import { Link as RouterLink } from "react-router-dom";
import { api, messageFor } from "../api";
import { useUser } from "../auth/AuthProvider";
import { money } from "../catalog";
import { formatDay } from "../time";

type Figure = "stage" | "overdue_filings" | "documents_missing" | "workload" | "fees_due" | "fees_overdue";

interface Dashboard {
  on: string;
  owner_id: number | null;
  stages: { stage: string; label: string; count: number }[];
  overdue_filings: number;
  documents_missing: number;
  workload: { id: number; full_name: string; role: "administrator" | "staff" | "consultant"; count: number }[];
  fees_due: { count: number; amount: string };
  fees_overdue: { count: number; amount: string };
}

interface ListResult {
  figure: Figure;
  key: string | null;
  total: number;
  cycles: { code: string; client_id: number; client_name: string; filing: string; period_label: string; due_date: string; stage: string; stage_label: string }[];
  invoices: { invoice_no: string; client_id: number; client_name: string; period_label: string; due_date: string; total: string; balance: string }[];
}

interface Open {
  figure: Figure;
  key: string | null;
  title: string;
}

function Tile({ label, value, detail, onOpen, selected }: { label: string; value: number; detail?: string; onOpen: () => void; selected: boolean }) {
  return (
    <ButtonBase
      onClick={onOpen}
      aria-pressed={selected}
      aria-label={`${label}: ${String(value)}${detail ? `, ${detail}` : ""}`}
      sx={{ display: "block", textAlign: "left", p: 2, border: 1, borderColor: selected ? "primary.main" : "divider", borderRadius: 3, width: "100%" }}
    >
      <Typography variant="h4" component="span" sx={{ display: "block", fontWeight: 700 }}>{value}</Typography>
      <Typography component="span" sx={{ display: "block" }}>{label}</Typography>
      {detail && <Typography variant="body2" component="span" color="text.secondary" sx={{ display: "block" }}>{detail}</Typography>}
    </ButtonBase>
  );
}

/** FR-RP-01, FR-RP-02: the figures, each opening the list behind it. Staff see only their own. */
export function Dashboard() {
  const user = useUser();
  const [data, setData] = useState<Dashboard | null>(null);
  const [open, setOpen] = useState<Open | null>(null);
  const [list, setList] = useState<ListResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Dashboard>("/api/dashboard").then(setData).catch((e: unknown) => { setError(messageFor(e)); });
  }, []);

  useEffect(() => {
    if (!open) return;
    const params = new URLSearchParams({ figure: open.figure });
    if (open.key) params.set("key", open.key);
    api<ListResult>(`/api/dashboard/list?${params.toString()}`).then(setList).catch((e: unknown) => { setError(messageFor(e)); });
  }, [open]);

  const show = (figure: Figure, title: string, key: string | null = null) => {
    setList(null);
    setOpen((o) => (o && o.figure === figure && o.key === key ? null : { figure, key, title }));
  };
  const isOpen = (figure: Figure, key: string | null = null) => open?.figure === figure && open.key === key;

  return (
    <Box component="section" aria-labelledby="dashboard-heading" sx={{ mb: 4 }}>
      <Typography id="dashboard-heading" variant="h5" component="h2" sx={{ mb: 1 }}>
        {user.role === "administrator" ? "The whole practice" : "Your clients"}
      </Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {data && (
        <>
          <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "1fr 1fr", md: "repeat(4, 1fr)" }, mb: 2 }}>
            <Tile label="Overdue filings" value={data.overdue_filings} selected={isOpen("overdue_filings")} onOpen={() => { show("overdue_filings", "Overdue filings"); }} />
            <Tile label="Documents missing" value={data.documents_missing} selected={isOpen("documents_missing")} onOpen={() => { show("documents_missing", "Documents missing"); }} />
            <Tile label="Fees due" value={data.fees_due.count} detail={money(data.fees_due.amount)} selected={isOpen("fees_due")} onOpen={() => { show("fees_due", "Fees due"); }} />
            <Tile label="Fees overdue" value={data.fees_overdue.count} detail={money(data.fees_overdue.amount)} selected={isOpen("fees_overdue")} onOpen={() => { show("fees_overdue", "Fees overdue"); }} />
          </Box>
          <Typography variant="h6" component="h3" sx={{ mb: 1 }}>Cycles by stage</Typography>
          <Box sx={{ display: "grid", gap: 1, gridTemplateColumns: { xs: "1fr 1fr", sm: "repeat(3, 1fr)", md: "repeat(5, 1fr)" }, mb: 2 }}>
            {data.stages.map((s) => (
              <Tile key={s.stage} label={s.label} value={s.count} selected={isOpen("stage", s.stage)} onOpen={() => { show("stage", `In ${s.label}`, s.stage); }} />
            ))}
          </Box>
          <Typography variant="h6" component="h3" sx={{ mb: 1 }}>Workload, next 30 days</Typography>
          {data.workload.length === 0 ? (
            <Typography color="text.secondary" sx={{ mb: 2 }}>No open work due in the next 30 days.</Typography>
          ) : (
            <List aria-label="Workload" dense disablePadding sx={{ border: 1, borderColor: "divider", borderRadius: 3, mb: 2 }}>
              {data.workload.map((p, i) => (
                <ListItemButton key={p.id} divider={i < data.workload.length - 1} selected={isOpen("workload", String(p.id))} onClick={() => { show("workload", `${p.full_name}'s work`, String(p.id)); }}>
                  <ListItemText primary={p.full_name} secondary={p.role === "consultant" ? "Consultant" : p.role === "administrator" ? "Administrator" : "Staff"} />
                  <Typography sx={{ fontWeight: 700 }}>{p.count}</Typography>
                </ListItemButton>
              ))}
            </List>
          )}
        </>
      )}
      {open && (
        <Paper variant="outlined" sx={{ borderRadius: 3, p: 2 }} aria-label={open.title} role="region">
          <Typography variant="h6" component="h3" sx={{ mb: 1 }}>{open.title}{list ? ` (${String(list.total)})` : ""}</Typography>
          {!list && <Typography color="text.secondary">Loading…</Typography>}
          {list?.total === 0 && <Typography color="text.secondary">Nothing here.</Typography>}
          {list && list.cycles.length > 0 && (
            <List dense disablePadding aria-label={`${open.title} list`}>
              {list.cycles.map((c) => (
                <ListItemButton key={c.code} component={RouterLink} to={`/tickets/${c.code}`}>
                  <ListItemText primary={`${c.client_name} · ${c.filing} ${c.period_label}`} secondary={`${c.code} · ${c.stage_label} · due ${formatDay(c.due_date)}`} />
                </ListItemButton>
              ))}
            </List>
          )}
          {list && list.invoices.length > 0 && (
            <List dense disablePadding aria-label={`${open.title} list`}>
              {list.invoices.map((inv) => (
                <ListItemButton key={inv.invoice_no} component={RouterLink} to={`/clients/${String(inv.client_id)}`}>
                  <ListItemText primary={`${inv.client_name} · ${inv.invoice_no}`} secondary={`${money(inv.balance)} of ${money(inv.total)} owed · due ${formatDay(inv.due_date)}`} />
                </ListItemButton>
              ))}
            </List>
          )}
          {list && list.total > list.cycles.length + list.invoices.length && (
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>Showing the first {list.cycles.length + list.invoices.length}.</Typography>
          )}
        </Paper>
      )}
    </Box>
  );
}
