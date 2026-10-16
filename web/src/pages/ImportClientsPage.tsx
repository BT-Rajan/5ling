import { useState, type ChangeEvent } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Link from "@mui/material/Link";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Typography from "@mui/material/Typography";
import { Link as RouterLink } from "react-router-dom";
import { api, messageFor } from "../api";
import { useUser } from "../auth/AuthProvider";

/** FR-CL-05 (C33): check a spreadsheet of clients, see every problem, then confirm to save. */

export const IMPORT_COLUMNS = [
  "source_id", "kind", "legal_name", "pan", "gstin", "owner_email", "package",
  "contact_name", "contact_email", "contact_phone", "whatsapp", "sms",
] as const;
const MAX_BYTES = 1024 * 1024;

interface RowProblem { line: number; source_id: string; problems: string[] }
interface CheckOut { rows: number; ready: number; problems: RowProblem[]; token: string }
interface SaveOut { saved: { line: number; source_id: string; client_id: number }[]; skipped: RowProblem[] }

function Problems({ rows, label }: { rows: RowProblem[]; label: string }) {
  if (rows.length === 0) return null;
  return (
    <Paper variant="outlined" sx={{ overflowX: "auto", mb: 2 }}>
      <Table size="small" aria-label={label}>
        <TableHead>
          <TableRow>
            <TableCell>Line</TableCell>
            <TableCell>Source ID</TableCell>
            <TableCell>Why</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.line}>
              <TableCell>{r.line}</TableCell>
              <TableCell>{r.source_id || "—"}</TableCell>
              <TableCell>
                {r.problems.map((p) => <Box key={p}>{p}</Box>)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Paper>
  );
}

export function ImportClientsPage() {
  const user = useUser();
  const isAdmin = user.role === "administrator";
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<CheckOut | null>(null);
  const [result, setResult] = useState<SaveOut | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function choose(e: ChangeEvent<HTMLInputElement>) {
    const picked = e.target.files?.[0] ?? null;
    setReport(null);
    setResult(null);
    setError(picked && picked.size > MAX_BYTES ? "The file is larger than 1 MB. Split it into smaller files." : null);
    setFile(picked && picked.size <= MAX_BYTES ? picked : null);
  }

  async function check() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      setReport(await api<CheckOut>("/api/clients/import/check", { method: "POST", csv: file, timeoutMs: 60_000 }));
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!file || !report) return;
    setBusy(true);
    setError(null);
    try {
      const path = `/api/clients/import/save?token=${encodeURIComponent(report.token)}`;
      setResult(await api<SaveOut>(path, { method: "POST", csv: file, timeoutMs: 300_000 }));
      setReport(null);
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  function template() {
    const blob = new Blob([IMPORT_COLUMNS.join(",") + "\r\n"], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "clients-template.csv";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => { URL.revokeObjectURL(url); }, 1000);
  }

  return (
    <>
      <Button component={RouterLink} to="/clients" size="small" sx={{ mb: 1, ml: -1 }}>← Clients</Button>
      <Typography variant="h1" component="h1" sx={{ mb: 2 }}>Import clients</Typography>
      <Typography sx={{ mb: 1 }}>
        Save the spreadsheet as <strong>CSV UTF-8</strong>, with a header row. Every row is checked first and nothing is saved until you confirm.
        Rows with problems are listed with the reason and are left out.
      </Typography>
      <Typography sx={{ mb: 1 }}>
        Columns (in any order): {IMPORT_COLUMNS.join(", ")}. Required: kind (individual or business), legal_name, package, contact_name and contact_email
        {isAdmin ? ", and owner_email (who owns the client)" : "; the clients will be yours"}. WhatsApp and SMS are yes or no and need contact_phone.
      </Typography>
      <Link component="button" type="button" onClick={template} sx={{ mb: 2, display: "inline-block" }}>Download an empty template</Link>

      <Stack direction={{ xs: "column", sm: "row" }} spacing={2} sx={{ alignItems: { sm: "center" }, mb: 2 }}>
        <Button variant="outlined" component="label">
          Choose file
          <input hidden type="file" accept=".csv,text/csv" aria-label="Spreadsheet file (CSV)" onChange={choose} />
        </Button>
        <Typography aria-live="polite">{file ? file.name : "No file chosen"}</Typography>
        <Button variant="contained" disabled={!file || busy} onClick={() => { void check(); }}>Check file</Button>
      </Stack>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      {report && (
        <section aria-label="Check result">
          <Alert severity={report.problems.length ? "warning" : "success"} sx={{ mb: 2 }}>
            {report.rows} rows checked: {report.ready} ready to save, {report.problems.length} with problems. Nothing has been saved yet.
          </Alert>
          <Problems rows={report.problems} label="Rows with problems" />
          {report.ready > 0 ? (
            <Button variant="contained" disabled={busy} onClick={() => { void save(); }}>
              Save {report.ready} {report.ready === 1 ? "client" : "clients"}
            </Button>
          ) : (
            <Typography>Fix the rows above in the spreadsheet, then check it again.</Typography>
          )}
        </section>
      )}

      {result && (
        <section aria-label="Import result">
          <Alert severity="success" sx={{ mb: 2 }}>
            {result.saved.length} {result.saved.length === 1 ? "client" : "clients"} saved; {result.skipped.length} {result.skipped.length === 1 ? "row" : "rows"} left out.
          </Alert>
          {result.saved.length > 0 && (
            <Paper variant="outlined" sx={{ overflowX: "auto", mb: 2 }}>
              <Table size="small" aria-label="Saved clients">
                <TableHead>
                  <TableRow>
                    <TableCell>Line</TableCell>
                    <TableCell>Source ID</TableCell>
                    <TableCell>Client ID</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {result.saved.map((s) => (
                    <TableRow key={s.line}>
                      <TableCell>{s.line}</TableCell>
                      <TableCell>{s.source_id || "—"}</TableCell>
                      <TableCell><Link component={RouterLink} to={`/clients/${String(s.client_id)}`}>{s.client_id}</Link></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Paper>
          )}
          <Problems rows={result.skipped} label="Rows left out" />
        </section>
      )}
    </>
  );
}
