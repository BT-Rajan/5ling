import { useCallback, useEffect, useState, type SyntheticEvent } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Divider from "@mui/material/Divider";
import IconButton from "@mui/material/IconButton";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { Link as RouterLink } from "react-router-dom";
import { api, messageFor } from "../api";
import { formatDay } from "../time";

interface Holiday {
  day: string;
  name: string;
}

interface LeadTimes {
  monthly_days: number;
  quarterly_days: number;
  yearly_days: number;
}

type Notice = { severity: "success" | "error"; text: string };

function LeadTimesForm() {
  const [values, setValues] = useState<Record<keyof LeadTimes, string> | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<LeadTimes>("/api/admin/lead-times")
      .then((l) => { setValues({ monthly_days: String(l.monthly_days), quarterly_days: String(l.quarterly_days), yearly_days: String(l.yearly_days) }); })
      .catch((e: unknown) => { setNotice({ severity: "error", text: messageFor(e) }); });
  }, []);

  async function save(event: SyntheticEvent) {
    event.preventDefault();
    if (!values) return;
    setBusy(true);
    setNotice(null);
    try {
      await api("/api/admin/lead-times", {
        method: "PUT",
        json: { monthly_days: Number(values.monthly_days), quarterly_days: Number(values.quarterly_days), yearly_days: Number(values.yearly_days) },
      });
      setNotice({ severity: "success", text: "Saved. Calendars without their own lead time use these from now on." });
    } catch (e) {
      setNotice({ severity: "error", text: messageFor(e) });
    } finally {
      setBusy(false);
    }
  }

  const field = (key: keyof LeadTimes, label: string) => (
    <TextField
      label={label}
      value={values?.[key] ?? ""}
      onChange={(e) => { if (values) setValues({ ...values, [key]: e.target.value }); }}
      slotProps={{ htmlInput: { inputMode: "numeric" } }}
      disabled={busy || !values}
    />
  );

  return (
    <Box component="form" onSubmit={(e) => void save(e)} aria-labelledby="lead-heading" sx={{ mb: 4, maxWidth: 720 }} noValidate>
      <Typography id="lead-heading" variant="h5" component="h2" sx={{ mb: 1 }}>Alert lead times</Typography>
      <Typography color="text.secondary" sx={{ mb: 2 }}>
        Days before the due date that the client is first alerted, for calendars that do not set their own.
      </Typography>
      {notice && <Alert severity={notice.severity} sx={{ mb: 2 }}>{notice.text}</Alert>}
      <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr 1fr" } }}>
        {field("monthly_days", "Monthly filings")}
        {field("quarterly_days", "Quarterly filings")}
        {field("yearly_days", "Yearly filings")}
      </Box>
      <Button type="submit" variant="outlined" sx={{ mt: 2 }} disabled={busy || !values}>Save lead times</Button>
    </Box>
  );
}

function HolidayList() {
  const [year, setYear] = useState(() => new Date().getFullYear());
  const [holidays, setHolidays] = useState<Holiday[] | null>(null);
  const [day, setDay] = useState("");
  const [name, setName] = useState("");
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api<Holiday[]>(`/api/admin/holidays?from_date=${String(year)}-01-01&to_date=${String(year)}-12-31`)
      .then(setHolidays)
      .catch((e: unknown) => { setNotice({ severity: "error", text: messageFor(e) }); });
  }, [year]);
  useEffect(load, [load]);

  async function add(event: SyntheticEvent) {
    event.preventDefault();
    setBusy(true);
    setNotice(null);
    try {
      await api("/api/admin/holidays", { method: "POST", json: { day, name: name.trim() } });
      setNotice({ severity: "success", text: `${formatDay(day)} added.` });
      setDay("");
      setName("");
      if (Number(day.slice(0, 4)) === year) load();
      else setYear(Number(day.slice(0, 4)));
    } catch (e) {
      setNotice({ severity: "error", text: messageFor(e) });
    } finally {
      setBusy(false);
    }
  }

  async function remove(h: Holiday) {
    setBusy(true);
    setNotice(null);
    try {
      await api(`/api/admin/holidays/${h.day}`, { method: "DELETE" });
      setNotice({ severity: "success", text: `${formatDay(h.day)} removed.` });
      load();
    } catch (e) {
      setNotice({ severity: "error", text: messageFor(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Box component="section" aria-labelledby="holidays-heading" sx={{ maxWidth: 720 }}>
      <Stack direction="row" sx={{ alignItems: "center", gap: 1, mb: 1, flexWrap: "wrap" }}>
        <Typography id="holidays-heading" variant="h5" component="h2" sx={{ mr: "auto" }}>Holidays in {year}</Typography>
        <Button size="small" onClick={() => { setYear((y) => y - 1); }}>{year - 1}</Button>
        <Button size="small" onClick={() => { setYear((y) => y + 1); }}>{year + 1}</Button>
      </Stack>
      <Typography color="text.secondary" sx={{ mb: 2 }}>
        Saturdays and Sundays are always treated as non-working days. A calendar either moves a date that falls on one to the working day before, or keeps it.
      </Typography>
      {notice && <Alert severity={notice.severity} sx={{ mb: 2 }}>{notice.text}</Alert>}
      <Box component="form" onSubmit={(e) => void add(e)} noValidate sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: "1fr 2fr auto" }, alignItems: "start", mb: 2 }}>
        <TextField label="Date" type="date" value={day} onChange={(e) => { setDay(e.target.value); }} slotProps={{ inputLabel: { shrink: true } }} disabled={busy} />
        <TextField label="Holiday" value={name} onChange={(e) => { setName(e.target.value); }} disabled={busy} />
        <Button type="submit" variant="contained" sx={{ height: 56 }} disabled={busy || !day || !name.trim()}>Add holiday</Button>
      </Box>
      {holidays === null && <Typography color="text.secondary">Loading…</Typography>}
      {holidays?.length === 0 && <Typography color="text.secondary">No holidays listed for {year}.</Typography>}
      {holidays && holidays.length > 0 && (
        <List aria-label={`Holidays in ${String(year)}`} disablePadding sx={{ border: 1, borderColor: "divider", borderRadius: 3 }}>
          {holidays.map((h, i) => (
            <ListItem
              key={h.day}
              divider={i < holidays.length - 1}
              secondaryAction={
                <IconButton edge="end" aria-label={`Remove ${h.name}`} onClick={() => void remove(h)} disabled={busy}>
                  <span aria-hidden="true">×</span>
                </IconButton>
              }
            >
              <ListItemText primary={h.name} secondary={formatDay(h.day)} />
            </ListItem>
          ))}
        </List>
      )}
    </Box>
  );
}

export function HolidaysPage() {
  return (
    <>
      <Button component={RouterLink} to="/setup" size="small" sx={{ mb: 1, ml: -1 }}>← Setup</Button>
      <Typography variant="h1" component="h1" sx={{ mb: 3, display: { xs: "none", md: "block" } }}>Holidays and lead times</Typography>
      <LeadTimesForm />
      <Divider sx={{ mb: 3 }} />
      <HolidayList />
    </>
  );
}
