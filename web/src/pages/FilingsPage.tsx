import { useCallback, useEffect, useState, type SyntheticEvent } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Divider from "@mui/material/Divider";
import FormControlLabel from "@mui/material/FormControlLabel";
import IconButton from "@mui/material/IconButton";
import List from "@mui/material/List";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemText from "@mui/material/ListItemText";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { Link as RouterLink, useNavigate, useParams } from "react-router-dom";
import { api, messageFor } from "../api";
import {
  BASIS_LABEL,
  FREQUENCY_LABEL,
  HOLIDAY_LABEL,
  calendarSummary,
  dueDayText,
  periodKeys,
  type Calendar,
  type ChecklistEntry,
  type FilingDetail,
  type FilingType,
  type Frequency,
  type HolidayRule,
  type PeriodBasis,
} from "../catalog";
import { ChecklistEditor, cleanChecklist } from "../components/ChecklistEditor";
import { formatIST } from "../time";

function BackLink({ to, children }: { to: string; children: string }) {
  return (
    <Button component={RouterLink} to={to} size="small" sx={{ mb: 1, ml: -1 }}>
      ← {children}
    </Button>
  );
}

function AddFilingDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: SyntheticEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api<FilingDetail>("/api/admin/filings", { method: "POST", json: { code: code.trim(), name: name.trim() } });
      void navigate(`/setup/filings/${created.id}`);
    } catch (e) {
      setError(messageFor(e));
      setBusy(false);
    }
  }

  return (
    <Dialog open onClose={busy ? undefined : onClose} fullWidth maxWidth="xs" aria-labelledby="add-filing-title">
      <form onSubmit={(e) => void submit(e)} noValidate>
        <DialogTitle id="add-filing-title">Add a filing type</DialogTitle>
        <DialogContent>
          <Stack spacing={2.5} sx={{ pt: 1 }}>
            {error && <Alert severity="error">{error}</Alert>}
            <TextField label="Code" value={code} onChange={(e) => { setCode(e.target.value); }} helperText="For example GSTR-1 or TDS 24Q" required autoFocus fullWidth />
            <TextField label="Name" value={name} onChange={(e) => { setName(e.target.value); }} required fullWidth />
          </Stack>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={onClose} disabled={busy}>Cancel</Button>
          <Button type="submit" variant="contained" disabled={busy || !code.trim() || !name.trim()}>Add</Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}

export function FilingsPage() {
  const [filings, setFilings] = useState<FilingType[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    api<FilingType[]>("/api/admin/filings").then(setFilings).catch((e: unknown) => { setError(messageFor(e)); });
  }, []);

  return (
    <>
      <BackLink to="/setup">Setup</BackLink>
      <Stack direction="row" sx={{ alignItems: "center", justifyContent: "space-between", mb: 2 }}>
        <Typography variant="h1" component="h1" sx={{ display: { xs: "none", md: "block" } }}>Filing calendars</Typography>
        <Button variant="contained" onClick={() => { setAdding(true); }} sx={{ ml: "auto" }}>Add filing type</Button>
      </Stack>
      {error && <Alert severity="error">{error}</Alert>}
      {filings === null && !error && <Typography color="text.secondary">Loading…</Typography>}
      {filings?.length === 0 && <Typography color="text.secondary">No filing types yet. Add one, then set its calendar.</Typography>}
      {filings && filings.length > 0 && (
        <List disablePadding aria-label="Filing types" sx={{ maxWidth: 720, border: 1, borderColor: "divider", borderRadius: 3 }}>
          {filings.map((f, i) => (
            <ListItemButton key={f.id} component={RouterLink} to={`/setup/filings/${f.id}`} divider={i < filings.length - 1} sx={{ py: 1.5, opacity: f.is_active ? 1 : 0.7 }}>
              <ListItemText
                primary={`${f.code} · ${f.name}`}
                secondary={f.current ? `${calendarSummary(f.current)} · version ${f.current.version}` : "No calendar published yet"}
                slotProps={{ primary: { sx: { fontWeight: 500 } } }}
              />
              <Stack direction="row" spacing={1}>
                {f.draft && <Chip size="small" label="Draft" />}
                {!f.is_active && <Chip size="small" label="Retired" />}
              </Stack>
            </ListItemButton>
          ))}
        </List>
      )}
      {adding && <AddFilingDialog onClose={() => { setAdding(false); }} />}
    </>
  );
}

interface CalendarForm {
  frequency: Frequency;
  period_basis: PeriodBasis;
  months_after: string;
  day: string;
  overrides: { key: string; months_after: string; day: string }[];
  lead_days: string;
  holiday_rule: HolidayRule;
  notes: string;
}

function formFrom(c: Calendar | null): CalendarForm {
  if (!c) {
    return { frequency: "monthly", period_basis: "financial_year", months_after: "1", day: "11", overrides: [], lead_days: "", holiday_rule: "earlier_working_day", notes: "" };
  }
  return {
    frequency: c.frequency,
    period_basis: c.period_basis,
    months_after: String(c.due.months_after),
    day: String(c.due.day),
    overrides: Object.entries(c.due.overrides).map(([key, d]) => ({ key, months_after: String(d.months_after), day: String(d.day) })),
    lead_days: c.lead_days === null ? "" : String(c.lead_days),
    holiday_rule: c.holiday_rule,
    notes: c.notes ?? "",
  };
}

function calendarBody(f: CalendarForm) {
  return {
    frequency: f.frequency,
    period_basis: f.period_basis,
    due: {
      months_after: Number(f.months_after),
      day: Number(f.day),
      overrides: Object.fromEntries(f.overrides.map((o) => [o.key, { months_after: Number(o.months_after), day: Number(o.day) }])),
    },
    lead_days: f.lead_days.trim() === "" ? null : Number(f.lead_days),
    holiday_rule: f.holiday_rule,
    notes: f.notes.trim() || null,
  };
}

function CalendarEditor({ filing, onSaved }: { filing: FilingDetail; onSaved: (notice: string) => void }) {
  const [form, setForm] = useState<CalendarForm>(() => formFrom(filing.draft ?? filing.current));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keys = periodKeys(form.frequency);
  const unusedKeys = keys.filter((k) => !form.overrides.some((o) => o.key === k));
  const base = `/api/admin/filings/${filing.id}/calendar`;

  function set<K extends keyof CalendarForm>(key: K, value: CalendarForm[K]) {
    setForm((f) => ({ ...f, [key]: value, ...(key === "frequency" ? { overrides: [] } : {}) }));
  }

  async function run(action: "save" | "publish" | "discard") {
    setBusy(true);
    setError(null);
    try {
      if (action === "discard") {
        await api(`${base}/draft`, { method: "DELETE" });
        onSaved("Draft discarded.");
        return;
      }
      const saved = await api<Calendar>(`${base}/draft`, { method: "PUT", json: calendarBody(form) });
      if (action === "publish") {
        await api(`${base}/publish`, { method: "POST" });
        onSaved(`Calendar version ${saved.version} published. New cycles will use it; existing cycles keep theirs.`);
      } else {
        onSaved(`Draft version ${saved.version} saved. It is not used until you publish it.`);
      }
    } catch (e) {
      setError(messageFor(e));
      setBusy(false);
    }
  }

  const n = (value: string) => ({ inputMode: "numeric" as const, pattern: "[0-9]*", "aria-valuetext": value });

  return (
    <Box component="section" aria-labelledby="calendar-heading" sx={{ mb: 4 }}>
      <Typography id="calendar-heading" variant="h5" component="h2" sx={{ mb: 1 }}>Calendar</Typography>
      <Typography color="text.secondary" sx={{ mb: 2 }}>
        {filing.draft ? `Editing draft version ${filing.draft.version}.` : filing.current ? `Saving starts version ${filing.current.version + 1}; version ${filing.current.version} stays as it is.` : "Saving starts version 1."}
      </Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" }, maxWidth: 720 }}>
        <TextField select label="Frequency" value={form.frequency} onChange={(e) => { set("frequency", e.target.value as Frequency); }} disabled={busy}>
          {(Object.keys(FREQUENCY_LABEL) as Frequency[]).map((k) => <MenuItem key={k} value={k}>{FREQUENCY_LABEL[k]}</MenuItem>)}
        </TextField>
        <TextField select label="Year" value={form.period_basis} onChange={(e) => { set("period_basis", e.target.value as PeriodBasis); }} disabled={busy}>
          {(Object.keys(BASIS_LABEL) as PeriodBasis[]).map((k) => <MenuItem key={k} value={k}>{BASIS_LABEL[k]}</MenuItem>)}
        </TextField>
        <TextField label="Due: months after the period" value={form.months_after} onChange={(e) => { set("months_after", e.target.value); }} slotProps={{ htmlInput: n(form.months_after) }} helperText="0 to 12" disabled={busy} />
        <TextField label="Due: day of that month" value={form.day} onChange={(e) => { set("day", e.target.value); }} slotProps={{ htmlInput: n(form.day) }} helperText="1 to 31; 31 means the last day" disabled={busy} />
        <TextField label="Alert lead time (days)" value={form.lead_days} onChange={(e) => { set("lead_days", e.target.value); }} slotProps={{ htmlInput: n(form.lead_days) }} helperText="Leave blank for the default (4 days monthly, 21 days yearly)" disabled={busy} />
        <TextField select label="If a date falls on a holiday" value={form.holiday_rule} onChange={(e) => { set("holiday_rule", e.target.value as HolidayRule); }} disabled={busy}>
          {(Object.keys(HOLIDAY_LABEL) as HolidayRule[]).map((k) => <MenuItem key={k} value={k}>{HOLIDAY_LABEL[k]}</MenuItem>)}
        </TextField>
      </Box>
      {keys.length > 0 && (
        <Box sx={{ mt: 2, maxWidth: 720 }}>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>Periods with their own due date</Typography>
          <Stack spacing={1}>
            {form.overrides.map((o, i) => (
              <Stack key={o.key} direction="row" spacing={1} sx={{ alignItems: "center" }}>
                <TextField select size="small" label="Period" value={o.key} sx={{ width: 110 }} disabled={busy}
                  onChange={(e) => { set("overrides", form.overrides.map((x, j) => (j === i ? { ...x, key: e.target.value } : x))); }}>
                  {[o.key, ...unusedKeys].map((k) => <MenuItem key={k} value={k}>{k}</MenuItem>)}
                </TextField>
                <TextField size="small" label="Months after" value={o.months_after} sx={{ width: 130 }} slotProps={{ htmlInput: n(o.months_after) }} disabled={busy}
                  onChange={(e) => { set("overrides", form.overrides.map((x, j) => (j === i ? { ...x, months_after: e.target.value } : x))); }} />
                <TextField size="small" label="Day" value={o.day} sx={{ width: 90 }} slotProps={{ htmlInput: n(o.day) }} disabled={busy}
                  onChange={(e) => { set("overrides", form.overrides.map((x, j) => (j === i ? { ...x, day: e.target.value } : x))); }} />
                <IconButton aria-label={`Remove ${o.key}`} onClick={() => { set("overrides", form.overrides.filter((_, j) => j !== i)); }} disabled={busy}>
                  <span aria-hidden="true">×</span>
                </IconButton>
              </Stack>
            ))}
          </Stack>
          {unusedKeys.length > 0 && (
            <Button size="small" sx={{ mt: 1 }} disabled={busy}
              onClick={() => { set("overrides", [...form.overrides, { key: unusedKeys[0] ?? "", months_after: form.months_after, day: form.day }]); }}>
              Add a period
            </Button>
          )}
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            {form.period_basis === "financial_year" ? "Counted from April: M1 is April, Q1 is April to June." : "Counted from January: M1 is January, Q1 is January to March."}
          </Typography>
        </Box>
      )}
      <TextField label="Notes" value={form.notes} onChange={(e) => { set("notes", e.target.value); }} fullWidth multiline minRows={2} sx={{ mt: 2, maxWidth: 720 }} disabled={busy} />
      <Stack direction="row" spacing={1} sx={{ mt: 2, flexWrap: "wrap", gap: 1 }}>
        <Button variant="contained" onClick={() => void run("publish")} disabled={busy}>Save and publish</Button>
        <Button variant="outlined" onClick={() => void run("save")} disabled={busy}>Save draft</Button>
        {filing.draft && <Button color="error" onClick={() => void run("discard")} disabled={busy}>Discard draft</Button>}
      </Stack>
    </Box>
  );
}

function FilingDetails({ filing, onSaved }: { filing: FilingDetail; onSaved: (notice: string) => void }) {
  const [name, setName] = useState(filing.name);
  const [description, setDescription] = useState(filing.description ?? "");
  const [active, setActive] = useState(filing.is_active);
  const [checklist, setChecklist] = useState<ChecklistEntry[]>(filing.default_checklist);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api(`/api/admin/filings/${filing.id}`, {
        method: "PATCH",
        json: { name: name.trim(), description: description.trim() || null, is_active: active, default_checklist: cleanChecklist(checklist) },
      });
      onSaved("Details saved. Packages keep their own copy of the checklist.");
    } catch (e) {
      setError(messageFor(e));
      setBusy(false);
    }
  }

  return (
    <Box component="section" aria-labelledby="details-heading" sx={{ mb: 4, maxWidth: 720 }}>
      <Typography id="details-heading" variant="h5" component="h2" sx={{ mb: 2 }}>Details</Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <Stack spacing={2}>
        <TextField label="Name" value={name} onChange={(e) => { setName(e.target.value); }} disabled={busy} />
        <TextField label="Description" value={description} onChange={(e) => { setDescription(e.target.value); }} multiline minRows={2} disabled={busy} />
        <ChecklistEditor label="Documents usually needed (copied into packages)" items={checklist} onChange={setChecklist} disabled={busy} />
        <FormControlLabel control={<Switch checked={active} onChange={(e) => { setActive(e.target.checked); }} disabled={busy} />} label="Can be added to packages" />
        <Box><Button variant="outlined" onClick={() => void save()} disabled={busy || !name.trim()}>Save details</Button></Box>
      </Stack>
    </Box>
  );
}

export function FilingPage() {
  const { filingId } = useParams();
  const [filing, setFiling] = useState<FilingDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [round, setRound] = useState(0);

  const load = useCallback(() => {
    api<FilingDetail>(`/api/admin/filings/${encodeURIComponent(filingId ?? "")}`)
      .then((f) => { setFiling(f); setRound((r) => r + 1); })
      .catch((e: unknown) => { setError(messageFor(e)); });
  }, [filingId]);
  useEffect(load, [load]);
  const saved = (text: string) => {
    setNotice(text);
    load();
  };

  if (error) return <Alert severity="error">{error}</Alert>;
  if (!filing) return <Typography color="text.secondary">Loading…</Typography>;
  return (
    <>
      <BackLink to="/setup/filings">Filing calendars</BackLink>
      <Typography variant="h1" component="h1" sx={{ mb: 3, fontSize: { xs: "1.75rem", md: undefined } }}>{filing.code}</Typography>
      {notice && <Alert severity="success" sx={{ mb: 3 }} onClose={() => { setNotice(null); }}>{notice}</Alert>}
      <CalendarEditor key={`c${round}`} filing={filing} onSaved={saved} />
      <Divider sx={{ mb: 3 }} />
      <FilingDetails key={`d${round}`} filing={filing} onSaved={saved} />
      <Divider sx={{ mb: 3 }} />
      <Typography variant="h5" component="h2" sx={{ mb: 1 }}>Versions</Typography>
      {filing.versions.length === 0 ? (
        <Typography color="text.secondary">No calendar yet.</Typography>
      ) : (
        <List aria-label="Calendar versions" disablePadding sx={{ maxWidth: 720 }}>
          {filing.versions.map((v) => (
            <ListItemText
              key={v.id}
              sx={{ py: 1 }}
              primary={`Version ${v.version} · ${v.status === "published" ? `published ${formatIST(v.published_at ?? "")}` : "draft"}`}
              secondary={`${FREQUENCY_LABEL[v.frequency]}, due ${dueDayText(v.due)}${v.lead_days === null ? "" : `, alert ${v.lead_days} days before`}`}
            />
          ))}
        </List>
      )}
    </>
  );
}
