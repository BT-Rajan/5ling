import { useEffect, useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { api, download, messageFor, saveFile } from "../api";
import { useUser } from "../auth/AuthProvider";

type ReportName = "filing-status" | "late-filings" | "fee-aging" | "consultant-turnaround" | "alert-delivery";

const REPORTS: Record<ReportName, { title: string; text: string; dated: boolean }> = {
  "filing-status": { title: "Filing status by period", text: "Each filing and period due in the range: not started, in progress, filed on time, filed late, overdue.", dated: true },
  "late-filings": { title: "Late filings", text: "Filed after the due date, with the reason given, or past due and not yet filed.", dated: true },
  "fee-aging": { title: "Fee aging", text: "What each client owes today, by how long it has been past due.", dated: false },
  "consultant-turnaround": { title: "Consultant turnaround", text: "For work assigned in the range: how long until it was sent for review.", dated: true },
  "alert-delivery": { title: "Alert delivery", text: "Client alerts and reminders sent in the range, and what became of their emails.", dated: true },
};

interface Report {
  report: ReportName;
  title: string;
  start: string;
  end: string;
  owner_id: number | null;
  columns: string[];
  rows: (string | number | null)[][];
  total: number;
  truncated: boolean;
}

/** FR-RP-03, FR-RP-04: reports on screen, and as a spreadsheet or PDF. Staff see their own. */
export function ReportsPage() {
  const user = useUser();
  const [name, setName] = useState<ReportName>("filing-status");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams();
    if (from) params.set("from", from);
    if (to) params.set("to", to);
    const query = params.toString();
    api<Report>(`/api/reports/${name}${query ? `?${query}` : ""}`)
      .then((r) => { setReport(r); setError(null); })
      .catch((e: unknown) => { setError(messageFor(e)); });
  }, [name, from, to]);

  async function exportAs(format: "xlsx" | "pdf") {
    setBusy(true);
    setError(null);
    try {
      saveFile(await download(`/api/reports/${name}/export`, { format, ...(from ? { from } : {}), ...(to ? { to } : {}) }));
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  const info = REPORTS[name];
  return (
    <>
      <Typography variant="h1" component="h1" sx={{ mb: 1, fontSize: { xs: "1.5rem", md: "2rem" } }}>Reports</Typography>
      <Typography color="text.secondary" sx={{ mb: 3 }}>
        {user.role === "administrator" ? "Every owner's clients." : "Your clients only."} Downloads are recorded in the audit log.
      </Typography>
      <Paper variant="outlined" sx={{ p: 2, borderRadius: 3, mb: 2 }}>
        <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", md: "2fr 1fr 1fr" } }}>
          <TextField select label="Report" value={name} onChange={(e) => { setReport(null); setName(e.target.value as ReportName); }}>
            {(Object.keys(REPORTS) as ReportName[]).map((r) => <MenuItem key={r} value={r}>{REPORTS[r].title}</MenuItem>)}
          </TextField>
          <TextField label="From" type="date" value={from} onChange={(e) => { setFrom(e.target.value); }} slotProps={{ inputLabel: { shrink: true } }} disabled={!info.dated} />
          <TextField label="To" type="date" value={to} onChange={(e) => { setTo(e.target.value); }} slotProps={{ inputLabel: { shrink: true } }} disabled={!info.dated} />
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
          {info.text}{info.dated && report ? ` From ${report.start} to ${report.end}.` : ""}
        </Typography>
        <Stack direction="row" sx={{ gap: 1, mt: 2, flexWrap: "wrap" }}>
          <Button variant="outlined" disabled={busy || !report} onClick={() => void exportAs("xlsx")}>Download spreadsheet</Button>
          <Button variant="outlined" disabled={busy || !report} onClick={() => void exportAs("pdf")}>Download PDF</Button>
        </Stack>
      </Paper>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {!report && !error && <Typography color="text.secondary">Loading…</Typography>}
      {report?.rows.length === 0 && <Typography color="text.secondary">Nothing to show for this range.</Typography>}
      {report && report.rows.length > 0 && (
        <TableContainer component={Paper} variant="outlined" sx={{ borderRadius: 3 }}>
          <Table size="small" aria-label={report.title}>
            <TableHead>
              <TableRow>{report.columns.map((c) => <TableCell key={c} sx={{ fontWeight: 600, whiteSpace: "nowrap" }}>{c}</TableCell>)}</TableRow>
            </TableHead>
            <TableBody>
              {report.rows.map((row, i) => (
                <TableRow key={i}>
                  {row.map((v, j) => <TableCell key={report.columns[j] ?? j} align={typeof v === "number" ? "right" : "left"}>{v ?? ""}</TableCell>)}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      {report?.truncated && <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>Showing the first {report.rows.length} of {report.total}. Download for all of them.</Typography>}
    </>
  );
}
