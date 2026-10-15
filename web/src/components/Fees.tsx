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

export type AdjustmentKind = "credit_note" | "discount" | "write_off";
const ADJUSTMENT_LABEL: Record<AdjustmentKind, string> = { credit_note: "Credit note", discount: "Discount", write_off: "Write-off" };

export interface Adjustment {
  id: number;
  client_id: number;
  client_name: string;
  invoice_id: number;
  invoice_no: string;
  kind: AdjustmentKind;
  kind_label: string;
  amount: string;
  reason: string;
  status: "pending" | "approved" | "rejected";
  requested_by: string | null;
  requested_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
  credit_note_no: string | null;
}

type PaymentMode = "bank_transfer" | "upi" | "cheque" | "cash" | "card" | "other";
const MODE_LABEL: Record<PaymentMode, string> = { bank_transfer: "Bank transfer", upi: "UPI", cheque: "Cheque", cash: "Cash", card: "Card", other: "Other" };

interface LedgerEntry {
  id: number;
  invoice_id: number;
  invoice_no: string;
  kind: "payment" | "reversal" | AdjustmentKind;
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
  adjustments: Adjustment[];
  status: FeeStatus | null;
  status_label: string | null;
  balance: string;
  overdue: string;
  overdue_limit: string | null;
  firm_limit: string;
  over_limit: boolean;
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
  const [adjusting, setAdjusting] = useState<Invoice | null>(null);

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
                            <Stack direction="row" sx={{ gap: 0.5 }}>
                              <Button size="small" onClick={() => { setAdjusting(null); setPaying(i); }} aria-label={`Record a payment for ${i.invoice_no}`}>Record payment</Button>
                              <Button size="small" onClick={() => { setPaying(null); setAdjusting(i); }} aria-label={`Adjust ${i.invoice_no}`}>Adjust</Button>
                            </Stack>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          )}
          <OverdueLimit clientId={clientId} fees={fees} onChanged={setFees} />
          {paying && <PaymentForm invoice={paying} onDone={(f) => { setFees(f); setPaying(null); }} onCancel={() => { setPaying(null); }} />}
          {adjusting && <AdjustmentForm invoice={adjusting} direct={fees.can_set_fee} onDone={(f) => { setFees(f); setAdjusting(null); }} onCancel={() => { setAdjusting(null); }} />}
          {fees.adjustments.length > 0 && (
            <>
              <Typography variant="h6" component="h3" sx={{ mt: 3, mb: 1 }}>Credit notes, discounts and write-offs</Typography>
              <List aria-label="Adjustments" disablePadding sx={{ border: 1, borderColor: "divider", borderRadius: 3 }}>
                {fees.adjustments.map((a, i) => (
                  <ListItem key={a.id} divider={i < fees.adjustments.length - 1}>
                    <ListItemText
                      primary={`${a.kind_label}${a.credit_note_no ? ` ${a.credit_note_no}` : ""} · ${money(a.amount)} · ${a.invoice_no} · ${ADJUSTMENT_STATUS[a.status]}`}
                      secondary={[`“${a.reason}”`, `asked by ${a.requested_by ?? "someone"}`, a.decided_by ? `${a.status === "approved" ? "approved" : "rejected"} by ${a.decided_by}` : null, a.decision_note ? `“${a.decision_note}”` : null].filter(Boolean).join(" · ")}
                    />
                  </ListItem>
                ))}
              </List>
            </>
          )}
          <Ledger entries={fees.ledger} onChanged={setFees} />
        </>
      )}
    </Box>
  );
}

const ADJUSTMENT_STATUS = { pending: "waiting for an Administrator", approved: "approved", rejected: "rejected" } as const;

/** FR-FE-08: a credit note, discount or write-off; Staff ask, an Administrator approves. */
function AdjustmentForm({ invoice, direct, onDone, onCancel }: { invoice: Invoice; direct: boolean; onDone: (f: Fees) => void; onCancel: () => void }) {
  const [kind, setKind] = useState<AdjustmentKind>("discount");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const valid = MONEY.test(amount.trim()) && Number(amount) > 0 && reason.trim().length >= 5;

  async function submit(event: SyntheticEvent) {
    event.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError(null);
    try {
      onDone(await api<Fees>(`/api/invoices/${String(invoice.id)}/adjustments`, { method: "POST", json: { kind, amount: amount.trim(), reason: reason.trim() } }));
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Paper component="form" aria-label={`Adjustment to ${invoice.invoice_no}`} onSubmit={(e) => void submit(e)} noValidate variant="outlined" sx={{ p: 2, borderRadius: 3, mt: 2 }}>
      <Typography sx={{ fontWeight: 600 }}>Adjust {invoice.invoice_no} · {money(invoice.balance)} owed</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{direct ? "Takes effect at once." : "Changes nothing until an Administrator approves it."}</Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" } }}>
        <TextField select label="Kind" value={kind} onChange={(e) => { setKind(e.target.value as AdjustmentKind); }} disabled={busy}>
          {(Object.keys(ADJUSTMENT_LABEL) as AdjustmentKind[]).map((k) => <MenuItem key={k} value={k}>{ADJUSTMENT_LABEL[k]}</MenuItem>)}
        </TextField>
        <TextField label="Adjustment (₹)" value={amount} onChange={(e) => { setAmount(e.target.value); }} slotProps={{ htmlInput: { inputMode: "decimal", maxLength: 11 } }} disabled={busy} />
        <TextField label="Why?" value={reason} onChange={(e) => { setReason(e.target.value); }} slotProps={{ htmlInput: { maxLength: 500 } }} disabled={busy} sx={{ gridColumn: { sm: "1 / -1" } }} />
      </Box>
      <Stack direction="row" sx={{ gap: 1, mt: 2 }}>
        <Button type="submit" variant="contained" disabled={busy || !valid}>{direct ? "Apply" : "Ask for approval"}</Button>
        <Button disabled={busy} onClick={onCancel}>Cancel</Button>
      </Stack>
    </Paper>
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
                : e.kind === "reversal"
                  ? `Reversal of ${money(e.amount)} · ${e.invoice_no}${e.note ? `: “${e.note}”` : ""}`
                  : `${ADJUSTMENT_LABEL[e.kind]} ${money(e.amount)} · ${e.invoice_no}${e.note ? `: “${e.note}”` : ""}`}
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

/** FR-FE-06, BRD Q3: overdue fees above the client's limit raise a warning on its filings. */
function OverdueLimit({ clientId, fees, onChanged }: { clientId: number; fees: Fees; onChanged: (f: Fees) => void }) {
  const [editing, setEditing] = useState(false);
  const [useFirm, setUseFirm] = useState(fees.overdue_limit === null);
  const [limit, setLimit] = useState(fees.overdue_limit ?? "");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const valid = (useFirm || /^\d{1,8}(\.\d{1,2})?$/.test(limit.trim())) && reason.trim().length >= 5;

  async function save(event: SyntheticEvent) {
    event.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError(null);
    try {
      onChanged(await api<Fees>(`/api/clients/${String(clientId)}/overdue-limit`, { method: "PUT", json: { limit: useFirm ? null : limit.trim(), reason: reason.trim() } }));
      setEditing(false);
      setReason("");
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  const current = fees.overdue_limit === null ? `${money(fees.firm_limit)} (the firm's limit)` : money(fees.overdue_limit);
  return (
    <Box sx={{ mb: 2 }} data-testid="overdue-limit">
      <Typography variant="body2" color={fees.over_limit ? "error" : "text.secondary"}>
        Overdue: {money(fees.overdue)} · limit {current}{fees.over_limit ? " · over the limit" : ""}
        {!editing && <Button size="small" sx={{ ml: 1 }} onClick={() => { setEditing(true); setUseFirm(fees.overdue_limit === null); setLimit(fees.overdue_limit ?? ""); }}>Change limit</Button>}
      </Typography>
      {editing && (
        <Paper component="form" aria-label="Overdue limit" onSubmit={(e) => void save(e)} noValidate variant="outlined" sx={{ p: 2, borderRadius: 3, mt: 1 }}>
          {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
          <Stack spacing={2}>
            <TextField select label="Limit for this client" value={useFirm ? "firm" : "own"} onChange={(e) => { setUseFirm(e.target.value === "firm"); }} disabled={busy}>
              <MenuItem value="firm">The firm's limit ({money(fees.firm_limit)})</MenuItem>
              <MenuItem value="own">Its own limit</MenuItem>
            </TextField>
            {!useFirm && <TextField label="Overdue limit (₹)" value={limit} onChange={(e) => { setLimit(e.target.value); }} slotProps={{ htmlInput: { inputMode: "decimal", maxLength: 11 } }} disabled={busy} helperText="A warning shows on this client's filings when more than this is overdue." />}
            <TextField label="Why the change?" value={reason} onChange={(e) => { setReason(e.target.value); }} slotProps={{ htmlInput: { maxLength: 500 } }} disabled={busy} />
            <Stack direction="row" sx={{ gap: 1 }}>
              <Button type="submit" variant="contained" disabled={busy || !valid}>Save limit</Button>
              <Button disabled={busy} onClick={() => { setEditing(false); }}>Cancel</Button>
            </Stack>
          </Stack>
        </Paper>
      )}
    </Box>
  );
}
