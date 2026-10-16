import { useEffect, useState } from "react";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Typography from "@mui/material/Typography";
import { Link as RouterLink } from "react-router-dom";
import { api, download, messageFor, saveFile } from "../api";
import { formatDay, formatIST } from "../time";

/** FR-AC-04 (C37): everyone with access to every client, for the Partners to certify quarterly. */

interface Row {
  client_id: number | null;
  client_name: string;
  client_status: string | null;
  person: string;
  email: string;
  role: string;
  access: string;
  since: string | null;
}
interface Review { generated_at: string; generated_by: string; rows: Row[] }

export function AccessReviewPage() {
  const [review, setReview] = useState<Review | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Review>("/api/admin/access-review")
      .then(setReview)
      .catch((e: unknown) => { setError(messageFor(e)); });
  }, []);

  async function exportAs(format: "xlsx" | "pdf") {
    setBusy(true);
    setError(null);
    try {
      saveFile(await download("/api/admin/access-review/export", { format }));
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button component={RouterLink} to="/setup" size="small" sx={{ mb: 1, ml: -1 }}>← Settings</Button>
      <Typography variant="h1" component="h1" sx={{ mb: 1 }}>Access review</Typography>
      <Typography sx={{ mb: 2 }}>
        Everyone who can reach each client, and how. Export it each quarter for the Partners to check and sign. The export shows when it was generated.
      </Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <Stack direction="row" spacing={1} sx={{ mb: 2, alignItems: "center", flexWrap: "wrap" }}>
        <Button variant="contained" disabled={busy} onClick={() => { void exportAs("xlsx"); }}>Export spreadsheet</Button>
        <Button variant="outlined" disabled={busy} onClick={() => { void exportAs("pdf"); }}>Export PDF</Button>
        {review && <Typography color="text.secondary" data-testid="generated">Generated {formatIST(review.generated_at)}</Typography>}
      </Stack>
      {review && (
        <Paper variant="outlined" sx={{ overflowX: "auto" }}>
          <Table size="small" aria-label="Access to clients">
            <TableHead>
              <TableRow>
                <TableCell>Client</TableCell>
                <TableCell>Person</TableCell>
                <TableCell>Role</TableCell>
                <TableCell>Access</TableCell>
                <TableCell>Since</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {review.rows.map((r, i) => (
                <TableRow key={i}>
                  <TableCell>{r.client_id === null ? r.client_name : `${String(r.client_id)} · ${r.client_name}${r.client_status === "closed" ? " (closed)" : ""}`}</TableCell>
                  <TableCell>{r.person}<Typography variant="body2" color="text.secondary">{r.email}</Typography></TableCell>
                  <TableCell>{r.role}</TableCell>
                  <TableCell>{r.access}</TableCell>
                  <TableCell>{r.since ? formatDay(r.since.slice(0, 10)) : "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      )}
    </>
  );
}
