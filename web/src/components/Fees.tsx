import { useCallback, useEffect, useState, type SyntheticEvent } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
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
import { api, messageFor } from "../api";
import { money } from "../catalog";
import { formatDay, formatIST } from "../time";

export interface FeeOverride {
  id: number;
  client_id: number;
  client_name: string;
  original: string;
  amount: string;
  reason: string;
  status: "pending" | "approved" | "rejected";
  requested_by: string | null;
  requested_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
}

interface Invoice {
  id: number;
  invoice_no: string;
  period_label: string;
  issue_date: string;
  due_date: string;
  description: string;
  amount: string;
  tax_rate: string;
  cgst: string;
  sgst: string;
  igst: string;
  tax: string;
  total: string;
}

interface Fees {
  plan: { package_name: string; package_version: number; frequency: "monthly" | "quarterly" | "yearly" | "one_time"; package_fee: string; fee: string; override: FeeOverride | null };
  pending: FeeOverride | null;
  overrides: FeeOverride[];
  invoices: Invoice[];
  can_set_fee: boolean;
}

const BILLED = { monthly: "a month", quarterly: "a quarter", yearly: "a year", one_time: "once" } as const;
const MONEY = /^\d{1,8}(\.\d{1,2})?$/;

/** FR-FE-01, FR-FE-02: the client's fee plan, a different fee for this client, and invoices. */
export function ClientFees({ clientId }: { clientId: number }) {
  const [fees, setFees] = useState<Fees | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Fees>(`/api/clients/${String(clientId)}/fees`).then(setFees).catch((e: unknown) => { setError(messageFor(e)); });
  }, [clientId]);
  useEffect(load, [load]);

  const valid = MONEY.test(amount.trim()) && Number(amount) > 0 && reason.trim().length >= 5;

  async function submit(event: SyntheticEvent) {
    event.preventDefault();
    if (!valid) return;
    setBusy(true);
    setFormError(null);
    try {
      setFees(await api<Fees>(`/api/clients/${String(clientId)}/fee-overrides`, { method: "POST", json: { amount: amount.trim(), reason: reason.trim() } }));
      setAsking(false);
      setAmount("");
      setReason("");
    } catch (e) {
      setFormError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Box component="section" aria-labelledby="fees-heading" sx={{ mt: 4 }}>
      <Typography id="fees-heading" variant="h5" component="h2" sx={{ mb: 1 }}>Fees</Typography>
      {error && <Alert severity="error">{error}</Alert>}
      {fees && (
        <>
          <Paper variant="outlined" sx={{ p: 2, borderRadius: 3, mb: 2 }} data-testid="fee-plan">
            <Typography sx={{ fontWeight: 600 }}>{money(fees.plan.fee)} {BILLED[fees.plan.frequency]}</Typography>
            <Typography variant="body2" color="text.secondary">
              {fees.plan.package_name} version {fees.plan.package_version}
              {fees.plan.override
                ? ` · package fee ${money(fees.plan.override.original)}, this client's fee approved by ${fees.plan.override.decided_by ?? "an Administrator"} on ${formatIST(fees.plan.override.decided_at ?? fees.plan.override.requested_at)}`
                : ""}
            </Typography>
            {fees.plan.override && <Typography variant="body2" sx={{ mt: 0.5 }}>“{fees.plan.override.reason}”</Typography>}
          </Paper>
          {fees.pending && (
            <Alert severity="info" sx={{ mb: 2 }}>
              {money(fees.pending.amount)} asked for by {fees.pending.requested_by ?? "someone"}, waiting for an Administrator: “{fees.pending.reason}”
            </Alert>
          )}
          {!fees.pending && !asking && (
            <Button size="small" sx={{ ml: -1, mb: 2 }} onClick={() => { setAsking(true); }}>
              {fees.can_set_fee ? "Set a fee for this client" : "Ask for a different fee"}
            </Button>
          )}
          {asking && (
            <Paper component="form" onSubmit={(e) => void submit(e)} noValidate variant="outlined" sx={{ p: 2, borderRadius: 3, mb: 2 }}>
              {formError && <Alert severity="error" sx={{ mb: 2 }}>{formError}</Alert>}
              <Stack spacing={2}>
                <TextField label="Client fee (₹)" value={amount} onChange={(e) => { setAmount(e.target.value); }} slotProps={{ htmlInput: { inputMode: "decimal", maxLength: 11 } }} disabled={busy} required />
                <TextField label="Why this fee?" value={reason} onChange={(e) => { setReason(e.target.value); }} slotProps={{ htmlInput: { maxLength: 500 } }} disabled={busy} required multiline minRows={2} helperText={fees.can_set_fee ? "Takes effect at once." : "An Administrator approves it before it is used."} />
                <Stack direction="row" sx={{ gap: 1 }}>
                  <Button type="submit" variant="contained" disabled={busy || !valid}>{fees.can_set_fee ? "Set fee" : "Ask for approval"}</Button>
                  <Button disabled={busy} onClick={() => { setAsking(false); }}>Cancel</Button>
                </Stack>
              </Stack>
            </Paper>
          )}
          <Typography variant="h6" component="h3" sx={{ mt: 2, mb: 1 }}>Invoices</Typography>
          {fees.invoices.length === 0 ? (
            <Typography color="text.secondary">No invoices yet. They are raised at the start of each billing period.</Typography>
          ) : (
            <TableContainer component={Paper} variant="outlined" sx={{ borderRadius: 3 }}>
              <Table size="small" aria-label="Invoices">
                <TableHead>
                  <TableRow>
                    <TableCell>Invoice</TableCell>
                    <TableCell>Period</TableCell>
                    <TableCell>Issued</TableCell>
                    <TableCell>Due</TableCell>
                    <TableCell align="right">Fee</TableCell>
                    <TableCell align="right">GST</TableCell>
                    <TableCell align="right">Total</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {fees.invoices.map((i) => {
                    const igst = i.igst !== "0.00";
                    return (
                      <TableRow key={i.id}>
                        <TableCell sx={{ whiteSpace: "nowrap" }}>{i.invoice_no}</TableCell>
                        <TableCell>{i.period_label}</TableCell>
                        <TableCell sx={{ whiteSpace: "nowrap" }}>{formatDay(i.issue_date)}</TableCell>
                        <TableCell sx={{ whiteSpace: "nowrap" }}>{formatDay(i.due_date)}</TableCell>
                        <TableCell align="right">{money(i.amount)}</TableCell>
                        <TableCell align="right" title={igst ? `IGST ${i.tax_rate}%` : `CGST ${money(i.cgst)} + SGST ${money(i.sgst)}`}>
                          {igst ? "IGST " : ""}{money(i.tax)} ({i.tax_rate}%)
                        </TableCell>
                        <TableCell align="right" sx={{ fontWeight: 600 }}>{money(i.total)}</TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </>
      )}
    </Box>
  );
}
