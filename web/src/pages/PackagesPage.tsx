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
import List from "@mui/material/List";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemText from "@mui/material/ListItemText";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { Link as RouterLink, useNavigate, useParams } from "react-router-dom";
import { api, messageFor } from "../api";
import {
  BILLING_LABEL,
  BILLING_OPTION,
  money,
  type BillingFrequency,
  type ChecklistEntry,
  type FilingType,
  type PackageDetail,
  type PackageSummary,
  type PackageVersion,
} from "../catalog";
import { ChecklistEditor, cleanChecklist } from "../components/ChecklistEditor";
import { formatIST } from "../time";
import { FilingChangesQueue } from "../components/ClientFilings";

type Notice = { severity: "success" | "error"; text: string };

function BackLink({ to, children }: { to: string; children: string }) {
  return (
    <Button component={RouterLink} to={to} size="small" sx={{ mb: 1, ml: -1 }}>
      ← {children}
    </Button>
  );
}

function AddPackageDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: SyntheticEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api<PackageDetail>("/api/admin/packages", { method: "POST", json: { name: name.trim() } });
      void navigate(`/setup/packages/${created.id}`);
    } catch (e) {
      setError(messageFor(e));
      setBusy(false);
    }
  }

  return (
    <Dialog open onClose={busy ? undefined : onClose} fullWidth maxWidth="xs" aria-labelledby="add-package-title">
      <form onSubmit={(e) => void submit(e)} noValidate>
        <DialogTitle id="add-package-title">Add a package</DialogTitle>
        <DialogContent>
          <Stack spacing={2.5} sx={{ pt: 1 }}>
            {error && <Alert severity="error">{error}</Alert>}
            <TextField label="Name" value={name} onChange={(e) => { setName(e.target.value); }} helperText="For example GST Monthly" required autoFocus fullWidth />
          </Stack>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={onClose} disabled={busy}>Cancel</Button>
          <Button type="submit" variant="contained" disabled={busy || !name.trim()}>Add</Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}

function summaryLine(p: PackageSummary): string {
  if (p.current_version === null) return "Not published yet";
  const billing = p.current_billing_frequency ? ` ${BILLING_LABEL[p.current_billing_frequency]}` : "";
  return `${money(p.current_fee)}${billing} · ${p.current_filings} filing${p.current_filings === 1 ? "" : "s"} · version ${p.current_version}`;
}

export function PackagesPage() {
  const [packages, setPackages] = useState<PackageSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    api<PackageSummary[]>("/api/admin/packages").then(setPackages).catch((e: unknown) => { setError(messageFor(e)); });
  }, []);

  return (
    <>
      <BackLink to="/setup">Setup</BackLink>
      <Stack direction="row" sx={{ alignItems: "center", justifyContent: "space-between", mb: 2 }}>
        <Typography variant="h1" component="h1" sx={{ display: { xs: "none", md: "block" } }}>Packages</Typography>
        <Button variant="contained" onClick={() => { setAdding(true); }} sx={{ ml: "auto" }}>Add package</Button>
      </Stack>
      <FilingChangesQueue />
      {error && <Alert severity="error">{error}</Alert>}
      {packages === null && !error && <Typography color="text.secondary">Loading…</Typography>}
      {packages?.length === 0 && <Typography color="text.secondary">No packages yet. Set up filing calendars first, then add a package.</Typography>}
      {packages && packages.length > 0 && (
        <List disablePadding aria-label="Packages" sx={{ maxWidth: 720, border: 1, borderColor: "divider", borderRadius: 3 }}>
          {packages.map((p, i) => (
            <ListItemButton key={p.id} component={RouterLink} to={`/setup/packages/${p.id}`} divider={i < packages.length - 1} sx={{ py: 1.5, opacity: p.is_active ? 1 : 0.7 }}>
              <ListItemText primary={p.name} secondary={summaryLine(p)} slotProps={{ primary: { sx: { fontWeight: 500 } } }} />
              <Stack direction="row" spacing={1}>
                {p.has_draft && <Chip size="small" label="Draft" />}
                {!p.is_active && <Chip size="small" label="Retired" />}
              </Stack>
            </ListItemButton>
          ))}
        </List>
      )}
      {adding && <AddPackageDialog onClose={() => { setAdding(false); }} />}
    </>
  );
}

interface DraftFiling {
  filing_id: number;
  code: string;
  name: string;
  checklist: ChecklistEntry[];
}

interface DraftForm {
  fee: string;
  billing: BillingFrequency | "";
  notes: string;
  filings: DraftFiling[];
}

function formFrom(v: PackageVersion | undefined): DraftForm {
  if (!v) return { fee: "", billing: "", notes: "", filings: [] };
  return {
    fee: v.fee ?? "",
    billing: v.billing_frequency ?? "",
    notes: v.notes ?? "",
    filings: v.filings.map((f) => ({ filing_id: f.filing_id, code: f.code, name: f.name, checklist: f.checklist.map(({ label, required }) => ({ label, required })) })),
  };
}

function DraftEditor({ pkg, onDone }: { pkg: PackageDetail; onDone: (n: Notice) => void }) {
  const draft = pkg.versions.find((v) => v.status === "draft");
  const current = pkg.versions.find((v) => v.status === "published");
  const [form, setForm] = useState<DraftForm>(() => formFrom(draft ?? current));
  const [catalog, setCatalog] = useState<FilingType[]>([]);
  const [busy, setBusy] = useState(false);
  const base = `/api/admin/packages/${pkg.id}`;

  useEffect(() => {
    api<FilingType[]>("/api/admin/filings").then(setCatalog).catch(() => undefined);
  }, []);

  const addable = catalog.filter((f) => f.is_active && !form.filings.some((d) => d.filing_id === f.id));

  function update(change: Partial<DraftForm>) {
    setForm((f) => ({ ...f, ...change }));
  }

  async function run(action: "save" | "publish" | "discard") {
    setBusy(true);
    try {
      if (action === "discard") {
        await api(`${base}/draft`, { method: "DELETE" });
        onDone({ severity: "success", text: "Draft discarded." });
        return;
      }
      const body = {
        fee: form.fee.trim() === "" ? null : form.fee.trim().replaceAll(",", ""),
        billing_frequency: form.billing === "" ? null : form.billing,
        notes: form.notes.trim() || null,
        filings: form.filings.map((f) => ({ filing_id: f.filing_id, checklist: cleanChecklist(f.checklist) })),
      };
      const saved = await api<PackageVersion>(`${base}/draft`, { method: "PUT", json: body });
      if (action === "save") {
        onDone({ severity: "success", text: `Draft version ${saved.version} saved. Clients do not get it until you publish it.` });
        return;
      }
      try {
        await api(`${base}/publish`, { method: "POST" });
        onDone({ severity: "success", text: `Version ${saved.version} published. New clients get it; existing cycles keep the version they were built from.` });
      } catch (e) {
        onDone({ severity: "error", text: `Draft version ${saved.version} saved, but not published: ${messageFor(e)}` });
      }
    } catch (e) {
      onDone({ severity: "error", text: messageFor(e) });
    }
  }

  return (
    <Box component="section" aria-labelledby="contents-heading" sx={{ mb: 4, maxWidth: 720 }}>
      <Typography id="contents-heading" variant="h5" component="h2" sx={{ mb: 1 }}>What the client gets</Typography>
      <Typography color="text.secondary" sx={{ mb: 2 }}>
        {draft ? `Editing draft version ${draft.version}.` : current ? `Saving starts version ${current.version + 1}; version ${current.version} stays as it is.` : "Nothing published yet."}
      </Typography>
      <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" } }}>
        <TextField label="Fee (₹)" value={form.fee} onChange={(e) => { update({ fee: e.target.value }); }} slotProps={{ htmlInput: { inputMode: "decimal" } }} disabled={busy} />
        <TextField select label="Billed" value={form.billing} onChange={(e) => { update({ billing: e.target.value as BillingFrequency }); }} disabled={busy}>
          {(Object.keys(BILLING_OPTION) as BillingFrequency[]).map((k) => <MenuItem key={k} value={k}>{BILLING_OPTION[k]}</MenuItem>)}
        </TextField>
      </Box>

      <Typography variant="subtitle1" component="h3" sx={{ mt: 3, mb: 1, fontWeight: 600 }}>Filings</Typography>
      {form.filings.length === 0 && <Typography color="text.secondary" sx={{ mb: 1 }}>No filings yet. A package needs at least one before it can be published.</Typography>}
      <Stack spacing={2}>
        {form.filings.map((f, i) => (
          <Paper key={f.filing_id} variant="outlined" sx={{ p: 2, borderRadius: 3 }}>
            <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center", mb: 1 }}>
              <Typography sx={{ fontWeight: 600 }}>{f.code} · {f.name}</Typography>
              <Button size="small" color="error" disabled={busy} onClick={() => { update({ filings: form.filings.filter((_, j) => j !== i) }); }}>
                Remove {f.code}
              </Button>
            </Stack>
            <ChecklistEditor
              label={`Documents for ${f.code}`}
              items={f.checklist}
              disabled={busy}
              onChange={(items) => { update({ filings: form.filings.map((x, j) => (j === i ? { ...x, checklist: items } : x)) }); }}
            />
          </Paper>
        ))}
      </Stack>
      {addable.length > 0 && (
        <TextField
          select
          label="Add a filing"
          value=""
          sx={{ mt: 2, minWidth: 260 }}
          disabled={busy}
          onChange={(e) => {
            const chosen = addable.find((f) => String(f.id) === e.target.value);
            if (chosen) update({ filings: [...form.filings, { filing_id: chosen.id, code: chosen.code, name: chosen.name, checklist: chosen.default_checklist }] });
          }}
        >
          {addable.map((f) => (
            <MenuItem key={f.id} value={String(f.id)}>{f.code} · {f.name}{f.current ? "" : " (no calendar yet)"}</MenuItem>
          ))}
        </TextField>
      )}
      <TextField label="Notes" value={form.notes} onChange={(e) => { update({ notes: e.target.value }); }} fullWidth multiline minRows={2} sx={{ mt: 3 }} disabled={busy} />
      <Stack direction="row" sx={{ mt: 2, flexWrap: "wrap", gap: 1 }}>
        <Button variant="contained" onClick={() => void run("publish")} disabled={busy}>Save and publish</Button>
        <Button variant="outlined" onClick={() => void run("save")} disabled={busy}>Save draft</Button>
        {draft && <Button color="error" onClick={() => void run("discard")} disabled={busy}>Discard draft</Button>}
      </Stack>
    </Box>
  );
}

function PackageDetails({ pkg, onDone }: { pkg: PackageDetail; onDone: (n: Notice) => void }) {
  const [name, setName] = useState(pkg.name);
  const [description, setDescription] = useState(pkg.description ?? "");
  const [active, setActive] = useState(pkg.is_active);
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    try {
      await api(`/api/admin/packages/${pkg.id}`, { method: "PATCH", json: { name: name.trim(), description: description.trim() || null, is_active: active } });
      onDone({ severity: "success", text: "Details saved." });
    } catch (e) {
      onDone({ severity: "error", text: messageFor(e) });
    }
  }

  return (
    <Box component="section" aria-labelledby="pkg-details-heading" sx={{ mb: 4, maxWidth: 720 }}>
      <Typography id="pkg-details-heading" variant="h5" component="h2" sx={{ mb: 2 }}>Details</Typography>
      <Stack spacing={2}>
        <TextField label="Name" value={name} onChange={(e) => { setName(e.target.value); }} disabled={busy} />
        <TextField label="Description" value={description} onChange={(e) => { setDescription(e.target.value); }} multiline minRows={2} disabled={busy} />
        <FormControlLabel control={<Switch checked={active} onChange={(e) => { setActive(e.target.checked); }} disabled={busy} />} label="Can be given to new clients" />
        <Box><Button variant="outlined" onClick={() => void save()} disabled={busy || !name.trim()}>Save details</Button></Box>
      </Stack>
    </Box>
  );
}

export function PackagePage() {
  const { packageId } = useParams();
  const [pkg, setPkg] = useState<PackageDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [round, setRound] = useState(0);

  const load = useCallback(() => {
    api<PackageDetail>(`/api/admin/packages/${encodeURIComponent(packageId ?? "")}`)
      .then((p) => { setPkg(p); setRound((r) => r + 1); })
      .catch((e: unknown) => { setError(messageFor(e)); });
  }, [packageId]);
  useEffect(load, [load]);

  const done = (n: Notice) => {
    setNotice(n);
    load();
  };

  if (error) return <Alert severity="error">{error}</Alert>;
  if (!pkg) return <Typography color="text.secondary">Loading…</Typography>;
  return (
    <>
      <BackLink to="/setup/packages">Packages</BackLink>
      <Typography variant="h1" component="h1" sx={{ mb: 3, fontSize: { xs: "1.75rem", md: undefined } }}>{pkg.name}</Typography>
      {notice && <Alert severity={notice.severity} sx={{ mb: 3 }} onClose={() => { setNotice(null); }}>{notice.text}</Alert>}
      <DraftEditor key={`e${round}`} pkg={pkg} onDone={done} />
      <Divider sx={{ mb: 3 }} />
      <PackageDetails key={`d${round}`} pkg={pkg} onDone={done} />
      <Divider sx={{ mb: 3 }} />
      <Typography variant="h5" component="h2" sx={{ mb: 1 }}>Versions</Typography>
      <List aria-label="Package versions" disablePadding sx={{ maxWidth: 720 }}>
        {pkg.versions.map((v) => (
          <ListItemText
            key={v.id}
            sx={{ py: 1 }}
            primary={`Version ${v.version} · ${v.status === "published" ? `published ${formatIST(v.published_at ?? "")}` : "draft"}`}
            secondary={`${money(v.fee)}${v.billing_frequency ? ` ${BILLING_LABEL[v.billing_frequency]}` : ""} · ${v.filings.map((f) => f.code).join(", ") || "no filings"}`}
          />
        ))}
      </List>
    </>
  );
}
