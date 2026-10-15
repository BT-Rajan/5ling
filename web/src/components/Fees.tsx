import { useCallback, useEffect, useState, type SyntheticEvent } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import MenuItem from "@mui/material/MenuItem";
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
import { formatDay, formatIST, todayIST } from "../time";

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
  paid: string;
  balance: string;
  status: FeeStatus;
  status_label: string;
}

export type FeeStatus = "paid" | "due" | "part_paid" | "overdue";
export const FEE_STATUS_LABEL: Record<FeeStatus, string> = { paid: "Paid", due: "Due", part_paid: "Part paid", overdue: "Overdue" };
const FEE_STATUS_COLOR = { paid: "success", due: "default", part_paid: "info", overdue: "error" } as const;

/** FR-FE-04: Paid, Due, Overdue or Part paid. */
export function FeeChip({ status }: { status: FeeStatus | null }) {
  if (!status) return null;
  return <Chip size="small" label={`Fees: ${FEE_STATUS_LABEL[status]}`} color={FEE_STATUS_COLOR[status]} variant={status === "due" ? "outlined" : "filled"} />;
}

type PaymentMode = "bank_transfer" | "upi" | "cheque" | "cash" | "card" | "other";
const MODE_LABEL: Record<PaymentMode, string> = { bank_transfer: "Bank transfer", upi: "UPI", cheque: "Cheque", cash: "Cash", card: "Card", other: "Other" };

interface LedgerEntry {
  id: number;
  invoice_id: number;
  invoice_no: string;
  kind: "payment" | "reversal";
  amount: string;
  entry_date: string;
  mode: PaymentMode | null;
  reference: string | null;
  note: string | null;
  reverses_id: number | null;
  reversed: boolean;
  by: string | null;
  at: string;
}

interface Fees {
  plan: { package_name: string; package_version: number; frequency: "monthly" | "quarterly" | "yearly" | "one_time"; package_fee: string; fee: string; override: FeeOverride | null };
  pending: FeeOverride | null;
  overrides: FeeOverride[];
  invoices: Invoice[];
  ledger: LedgerEntry[];
  status: FeeStatus | null;
  status_label: string | null;
  balance: string;
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
  const [paying, setPaying] = useState<Invoice | null>(null);

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
      <Stack direction="row" sx={{ alignItems: "center", gap: 2, mb: 1, flexWrap: "wrap" }}>
        <Typography id="fees-heading" variant="h5" component="h2">Fees</Typography>
        {fees && <FeeChip status={fees.status} />}
        {fees && fees.balance !== "0.00" && <Typography color="text.secondary" data-testid="fee-balance">{money(fees.balance)} owed</Typography>}
      </Stack>
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
                    <TableCell align="right">Balance</TableCell>
                    <TableCell>Status</TableCell>
                    <TableCell aria-label="Actions" />
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
                        <TableCell align="right">{money(i.balance)}</TableCell>
                        <TableCell>{i.status_label}</TableCell>
                        <TableCell>
                          {i.balance !== "0.00" && (
                            <Button size="small" onClick={() => { setPaying(i); }} aria-label={`Record a payment for ${i.invoice_no}`}>Record payment</Button>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          )}
          {paying && <PaymentForm invoice={paying} onDone={(f) => { setFees(f); setPaying(null); }} onCancel={() => { setPaying(null); }} />}
          <Ledger entries={fees.ledger} onChanged={setFees} />
        </>
      )}
    </Box>
  );
}

/** FR-FE-03: a payment or part payment against one invoice. */
function PaymentForm({ invoice, onDone, onCancel }: { invoice: Invoice; onDone: (f: Fees) => void; onCancel: () => void }) {
  const [amount, setAmount] = useState(invoice.balance);
  const [paidOn, setPaidOn] = useState(todayIST());
  const [mode, setMode] = useState<PaymentMode>("bank_transfer");
  const [reference, setReference] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const valid = MONEY.test(amount.trim()) && Number(amount) > 0 && paidOn !== "";

  async function submit(event: SyntheticEvent) {
    event.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError(null);
    try {
      onDone(await api<Fees>(`/api/invoices/${String(invoice.id)}/payments`, { method: "POST", json: { amount: amount.trim(), paid_on: paidOn, mode, reference: reference.trim() || null } }));
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Paper component="form" aria-label={`Payment for ${invoice.invoice_no}`} onSubmit={(e) => void submit(e)} noValidate variant="outlined" sx={{ p: 2, borderRadius: 3, mt: 2 }}>
      <Typography sx={{ fontWeight: 600, mb: 2 }}>Payment for {invoice.invoice_no} · {money(invoice.balance)} owed</Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" } }}>
        <TextField label="Amount (₹)" value={amount} onChange={(e) => { setAmount(e.target.value); }} slotProps={{ htmlInput: { inputMode: "decimal", maxLength: 11 } }} disabled={busy} helperText="Less than the balance records a part payment." />
        <TextField label="Paid on" type="date" value={paidOn} onChange={(e) => { setPaidOn(e.target.value); }} slotProps={{ inputLabel: { shrink: true }, htmlInput: { max: todayIST() } }} disabled={busy} />
        <TextField select label="Mode" value={mode} onChange={(e) => { setMode(e.target.value as PaymentMode); }} disabled={busy}>
          {(Object.keys(MODE_LABEL) as PaymentMode[]).map((m) => <MenuItem key={m} value={m}>{MODE_LABEL[m]}</MenuItem>)}
        </TextField>
        <TextField label="Reference (optional)" value={reference} onChange={(e) => { setReference(e.target.value); }} slotProps={{ htmlInput: { maxLength: 100 } }} disabled={busy} helperText="UTR, cheque number or receipt" />
      </Box>
      <Stack direction="row" sx={{ gap: 1, mt: 2 }}>
        <Button type="submit" variant="contained" disabled={busy || !valid}>Record payment</Button>
        <Button disabled={busy} onClick={onCancel}>Cancel</Button>
      </Stack>
    </Paper>
  );
}

/** FR-FE-03: every payment and reversal, newest first. Nothing is edited or removed. */
function Ledger({ entries, onChanged }: { entries: LedgerEntry[]; onChanged: (f: Fees) => void }) {
  const [reversing, setReversing] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function reverse(id: number) {
    setBusy(true);
    setError(null);
    try {
      onChanged(await api<Fees>(`/api/ledger/${String(id)}/reverse`, { method: "POST", json: { reason: reason.trim() } }));
      setReversing(null);
      setReason("");
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  if (entries.length === 0) return null;
  return (
    <>
      <Typography variant="h6" component="h3" sx={{ mt: 3, mb: 1 }}>Payments</Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <List aria-label="Payments" disablePadding sx={{ border: 1, borderColor: "divider", borderRadius: 3 }}>
        {entries.map((e, i) => (
          <ListItem key={e.id} divider={i < entries.length - 1} sx={{ display: "block" }}>
            <ListItemText
              primary={e.kind === "payment"
                ? `${money(e.amount)} paid ${formatDay(e.entry_date)} · ${e.mode ? MODE_LABEL[e.mode] : ""}${e.reference ? ` ${e.reference}` : ""} · ${e.invoice_no}${e.reversed ? " · reversed" : ""}`
                : `Reversal of ${money(e.amount)} · ${e.invoice_no}${e.note ? `: “${e.note}”` : ""}`}
              secondary={`${e.by ?? "Someone"}, ${formatIST(e.at)}`}
              sx={e.reversed ? { "& .MuiListItemText-primary": { textDecoration: "line-through" } } : undefined}
            />
            {e.kind === "payment" && !e.reversed && reversing !== e.id && (
              <Button size="small" color="error" sx={{ ml: -1 }} onClick={() => { setReversing(e.id); setReason(""); }}>Reverse</Button>
            )}
            {reversing === e.id && (
              <Stack spacing={1} sx={{ mt: 1 }}>
                <TextField label="Why is it reversed?" value={reason} onChange={(ev) => { setReason(ev.target.value); }} slotProps={{ htmlInput: { maxLength: 500 } }} disabled={busy} autoFocus size="small" />
                <Stack direction="row" sx={{ gap: 1 }}>
                  <Button color="error" variant="contained" size="small" disabled={busy || reason.trim().length < 5} onClick={() => void reverse(e.id)}>Reverse payment</Button>
                  <Button size="small" disabled={busy} onClick={() => { setReversing(null); }}>Cancel</Button>
                </Stack>
              </Stack>
            )}
          </ListItem>
        ))}
      </List>
    </>
  );
}
