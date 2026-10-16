import { useEffect, useState, type SyntheticEvent } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import FormControlLabel from "@mui/material/FormControlLabel";
import List from "@mui/material/List";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemText from "@mui/material/ListItemText";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Radio from "@mui/material/Radio";
import RadioGroup from "@mui/material/RadioGroup";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { Link as RouterLink, useLocation, useNavigate, useParams } from "react-router-dom";
import { formatDay, formatIST } from "../time";
import { BreakGlass, reasonRequiredFor } from "../components/BreakGlass";
import { ClientFees, FEE_STATUS_LABEL, type FeeStatus } from "../components/Fees";
import { api, ApiError, messageFor } from "../api";
import { ClientFilings } from "../components/ClientFilings";
import { ClientUpdates } from "../components/ClientUpdates";
import { useUser } from "../auth/AuthProvider";
import { BILLING_LABEL, money, type BillingFrequency } from "../catalog";

type Kind = "individual" | "business";
type Status = "active" | "paused" | "closed";

interface Consent {
  email: boolean;
  whatsapp: boolean;
  sms: boolean;
}

interface Contact {
  id: number;
  name: string;
  email: string;
  phone: string | null;
  is_primary: boolean;
  consent: Consent;
}

interface Client {
  client_id: number;
  kind: Kind;
  legal_name: string;
  pan_masked: string | null; // NFR-SE-06: the full PAN comes only on request, and is logged
  gstin: string | null;
  status: Status;
  owner: { id: number; full_name: string };
  package: { id: number; name: string; version: number };
  contacts: Contact[];
  created_at: string;
}

interface Options {
  owners: { id: number; full_name: string }[];
  packages: { id: number; name: string; version: number; fee: string | null; billing_frequency: BillingFrequency | null }[];
}

interface Existing {
  client_id: number;
  matched: "pan" | "gstin";
  can_open: boolean;
  legal_name: string | null;
}

const KIND_LABEL: Record<Kind, string> = { individual: "Individual", business: "Business" };

/** PAN is masked on screen unless needed (BRD security requirements). */
/** Plain sentences from a validation failure: "PAN: That is not a valid PAN." */
function problemsFrom(error: unknown): string[] {
  if (!(error instanceof ApiError) || error.code !== "validation_error") return [messageFor(error)];
  const fields = (error.body as { error?: { fields?: { where: (string | number)[]; problem: string }[] } } | null)?.error?.fields ?? [];
  const names: Record<string, string> = { legal_name: "Legal name", pan: "PAN", gstin: "GSTIN", package_id: "Package", contacts: "Contacts", owner_id: "Owner", email: "Email", phone: "Mobile", name: "Name" };
  return fields.map((f) => {
    const key = [...f.where].reverse().find((w) => typeof w === "string" && w !== "body");
    const label = typeof key === "string" ? (names[key] ?? key) : "";
    const text = f.problem.replace(/^Value error, /, "");
    return label ? `${label}: ${text}` : text;
  });
}

interface ClientRow {
  client_id: number;
  kind: Kind;
  legal_name: string;
  pan_masked: string | null;
  gstin: string | null;
  status: Status;
  owner: { id: number; full_name: string };
  package: { id: number; name: string; version: number };
  fee_status: FeeStatus | null;
}

interface ClientPageResult {
  items: ClientRow[];
  total: number;
  offset: number;
  limit: number;
}

const STATUS_LABEL: Record<Status, string> = { active: "Active", paused: "Paused", closed: "Closed" };
const PAGE = 50;

export function ClientsHome() {
  const me = useUser();
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [owner, setOwner] = useState("");
  const [pkg, setPkg] = useState("");
  const [options, setOptions] = useState<Options | null>(null);
  const [result, setResult] = useState<ClientPageResult | null>(null);
  const [rows, setRows] = useState<ClientRow[]>([]);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const isAdmin = me.role === "administrator";

  useEffect(() => {
    api<Options>("/api/clients/options").then(setOptions).catch(() => undefined);
  }, []);

  const params = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
  if (query.trim()) params.set("q", query.trim());
  if (status) params.set("status", status);
  if (owner) params.set("owner_id", owner);
  if (pkg) params.set("package_id", pkg);
  const url = `/api/clients?${params.toString()}`;

  useEffect(() => {
    let live = true;
    const timer = setTimeout(() => {
      api<ClientPageResult>(url)
        .then((page) => {
          if (!live) return;
          setError(null);
          setResult(page);
          setRows((old) => (page.offset === 0 ? page.items : [...old, ...page.items]));
        })
        .catch((e: unknown) => { if (live) setError(messageFor(e)); });
    }, 250);
    return () => { live = false; clearTimeout(timer); };
  }, [url]);

  function filter(set: (v: string) => void) {
    return (value: string) => {
      setOffset(0);
      set(value);
    };
  }

  return (
    <>
      <Stack direction="row" sx={{ alignItems: "center", justifyContent: "space-between", mb: 2 }}>
        <Typography variant="h1" component="h1" sx={{ display: { xs: "none", md: "block" } }}>Clients</Typography>
        <Stack direction="row" spacing={1} sx={{ ml: "auto" }}>
          <Button variant="outlined" component={RouterLink} to="/clients/import">Import</Button>
          <Button variant="contained" component={RouterLink} to="/clients/new">Add client</Button>
        </Stack>
      </Stack>
      <Box sx={{ display: "grid", gap: 2, mb: 2, gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr", md: isAdmin ? "2fr 1fr 1fr 1fr" : "2fr 1fr 1fr" } }}>
        <TextField label="Search" type="search" value={query} onChange={(e) => { filter(setQuery)(e.target.value); }} helperText="Name, PAN, GSTIN or client ID" />
        <TextField select label="Status" value={status} onChange={(e) => { filter(setStatus)(e.target.value); }}>
          <MenuItem value="">Any status</MenuItem>
          {(Object.keys(STATUS_LABEL) as Status[]).map((k) => <MenuItem key={k} value={k}>{STATUS_LABEL[k]}</MenuItem>)}
        </TextField>
        <TextField select label="Package" value={pkg} onChange={(e) => { filter(setPkg)(e.target.value); }}>
          <MenuItem value="">Any package</MenuItem>
          {(options?.packages ?? []).map((p) => <MenuItem key={p.id} value={String(p.id)}>{p.name}</MenuItem>)}
        </TextField>
        {isAdmin && (
          <TextField select label="Owner" value={owner} onChange={(e) => { filter(setOwner)(e.target.value); }}>
            <MenuItem value="">Anyone</MenuItem>
            {(options?.owners ?? []).map((o) => <MenuItem key={o.id} value={String(o.id)}>{o.full_name}</MenuItem>)}
          </TextField>
        )}
      </Box>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {result === null && !error && <Typography color="text.secondary">Loading…</Typography>}
      {result && rows.length === 0 && (
        <Typography color="text.secondary">{result.total === 0 && !query && !status && !pkg && !owner ? (isAdmin ? "No clients yet." : "You have no clients yet.") : "No clients match."}</Typography>
      )}
      {rows.length > 0 && (
        <>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>{result?.total} client{result?.total === 1 ? "" : "s"}</Typography>
          <List aria-label="Clients" disablePadding sx={{ border: 1, borderColor: "divider", borderRadius: 3 }}>
            {rows.map((c, i) => (
              <ListItemButton key={c.client_id} component={RouterLink} to={`/clients/${String(c.client_id)}`} divider={i < rows.length - 1} sx={{ py: 1.25, opacity: c.status === "closed" ? 0.7 : 1 }}>
                <ListItemText
                  primary={`${String(c.client_id)} · ${c.legal_name}`}
                  secondary={[KIND_LABEL[c.kind], c.pan_masked ?? c.gstin, c.package.name, isAdmin ? c.owner.full_name : null, c.status === "active" ? null : STATUS_LABEL[c.status], c.fee_status ? `Fees: ${FEE_STATUS_LABEL[c.fee_status]}` : null].filter(Boolean).join(" · ")}
                  slotProps={{ primary: { sx: { fontWeight: 500 } } }}
                />
              </ListItemButton>
            ))}
          </List>
          {result && rows.length < result.total && (
            <Box sx={{ mt: 2, textAlign: "center" }}>
              <Button onClick={() => { setOffset(rows.length); }}>Show more</Button>
            </Box>
          )}
        </>
      )}
    </>
  );
}

interface ContactForm {
  name: string;
  email: string;
  phone: string;
  whatsapp: boolean;
  sms: boolean;
}

const emptyContact = (): ContactForm => ({ name: "", email: "", phone: "", whatsapp: false, sms: false });

export function NewClientPage() {
  const me = useUser();
  const navigate = useNavigate();
  const [options, setOptions] = useState<Options | null>(null);
  const [kind, setKind] = useState<Kind>("individual");
  const [legalName, setLegalName] = useState("");
  const [pan, setPan] = useState("");
  const [gstin, setGstin] = useState("");
  const [owner, setOwner] = useState("");
  const [pkg, setPkg] = useState("");
  const [contacts, setContacts] = useState<ContactForm[]>([emptyContact()]);
  const [primary, setPrimary] = useState(0);
  const [problems, setProblems] = useState<string[]>([]);
  const [existing, setExisting] = useState<{ message: string; client: Existing } | null>(null);
  const [busy, setBusy] = useState(false);
  const isAdmin = me.role === "administrator";

  useEffect(() => {
    api<Options>("/api/clients/options")
      .then((o) => {
        setOptions(o);
        if (!isAdmin) setOwner(String(me.id));
      })
      .catch((e: unknown) => { setProblems([messageFor(e)]); });
  }, [isAdmin, me.id]);

  function updateContact(index: number, change: Partial<ContactForm>) {
    setContacts((list) => list.map((c, i) => (i === index ? { ...c, ...change } : c)));
  }

  async function save(event: SyntheticEvent) {
    event.preventDefault();
    setBusy(true);
    setProblems([]);
    setExisting(null);
    try {
      const created = await api<Client>("/api/clients", {
        method: "POST",
        json: {
          kind,
          legal_name: legalName,
          pan: pan.trim() || null,
          gstin: kind === "business" ? gstin.trim() || null : null,
          owner_id: owner ? Number(owner) : null,
          package_id: pkg ? Number(pkg) : null,
          contacts: contacts.map((c, i) => ({
            name: c.name,
            email: c.email.trim(),
            phone: c.phone.trim() || null,
            is_primary: i === primary,
            whatsapp: c.whatsapp,
            sms: c.sms,
          })),
        },
      });
      void navigate(`/clients/${String(created.client_id)}`, { state: { created: true } });
    } catch (e) {
      if (e instanceof ApiError && e.code === "duplicate_client") {
        const found = (e.body as { error: { existing: Existing } }).error.existing;
        setExisting({ message: e.message, client: found });
      } else {
        setProblems(problemsFrom(e));
      }
      setBusy(false);
    }
  }

  const ready = legalName.trim() && pkg && owner && contacts.every((c) => c.name.trim() && c.email.trim());

  return (
    <Box component="form" onSubmit={(e) => void save(e)} noValidate sx={{ maxWidth: 720 }}>
      <Button component={RouterLink} to="/clients" size="small" sx={{ mb: 1, ml: -1 }}>← Clients</Button>
      <Typography variant="h1" component="h1" sx={{ mb: 3, fontSize: { xs: "1.75rem", md: undefined } }}>Add a client</Typography>

      {existing && (
        <Alert severity="warning" sx={{ mb: 2 }} action={existing.client.can_open ? <Button color="inherit" component={RouterLink} to={`/clients/${String(existing.client.client_id)}`}>Open client {existing.client.client_id}</Button> : undefined}>
          {existing.message}
        </Alert>
      )}
      {problems.length > 0 && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {problems.length === 1 ? problems[0] : <Box component="ul" sx={{ m: 0, pl: 2 }}>{problems.map((p) => <li key={p}>{p}</li>)}</Box>}
        </Alert>
      )}

      <Stack spacing={2.5}>
        <RadioGroup row aria-label="Type of client" value={kind} onChange={(e) => { setKind(e.target.value as Kind); }}>
          <FormControlLabel value="individual" control={<Radio />} label="Individual" />
          <FormControlLabel value="business" control={<Radio />} label="Business" />
        </RadioGroup>
        <TextField label="Legal name" value={legalName} onChange={(e) => { setLegalName(e.target.value); }} required disabled={busy} />
        <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: kind === "business" ? "1fr 1fr" : "1fr" } }}>
          <TextField label="PAN" value={pan} onChange={(e) => { setPan(e.target.value.toUpperCase()); }} helperText="10 characters, like ABCPE1234F" slotProps={{ htmlInput: { autoCapitalize: "characters", spellCheck: false, autoComplete: "off" } }} disabled={busy} />
          {kind === "business" && (
            <TextField label="GSTIN" value={gstin} onChange={(e) => { setGstin(e.target.value.toUpperCase()); }} helperText="If registered: 15 characters" slotProps={{ htmlInput: { autoCapitalize: "characters", spellCheck: false, autoComplete: "off" } }} disabled={busy} />
          )}
        </Box>
        <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" } }}>
          {isAdmin ? (
            <TextField select label="Owner" value={owner} onChange={(e) => { setOwner(e.target.value); }} required disabled={busy || !options}>
              {(options?.owners ?? []).map((o) => <MenuItem key={o.id} value={String(o.id)}>{o.full_name}</MenuItem>)}
            </TextField>
          ) : (
            <TextField label="Owner" value={`${me.full_name} (you)`} disabled />
          )}
          <TextField select label="Package" value={pkg} onChange={(e) => { setPkg(e.target.value); }} required disabled={busy || !options}
            helperText={options?.packages.length === 0 ? "No package has been published yet. An Administrator sets them up under Setup." : " "}>
            {(options?.packages ?? []).map((p) => (
              <MenuItem key={p.id} value={String(p.id)}>{p.name} · {money(p.fee)}{p.billing_frequency ? ` ${BILLING_LABEL[p.billing_frequency]}` : ""}</MenuItem>
            ))}
          </TextField>
        </Box>

        <Typography variant="h5" component="h2">Contacts</Typography>
        {contacts.map((c, i) => (
          <Paper key={i} variant="outlined" sx={{ p: 2, borderRadius: 3 }}>
            <Stack spacing={2}>
              <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" } }}>
                <TextField label={`Contact ${String(i + 1)} name`} value={c.name} onChange={(e) => { updateContact(i, { name: e.target.value }); }} required disabled={busy} />
                <TextField label={`Contact ${String(i + 1)} email`} type="email" value={c.email} onChange={(e) => { updateContact(i, { email: e.target.value }); }} slotProps={{ htmlInput: { inputMode: "email", autoCapitalize: "none" } }} required disabled={busy} />
                <TextField label={`Contact ${String(i + 1)} mobile`} type="tel" value={c.phone} onChange={(e) => { updateContact(i, { phone: e.target.value, ...(e.target.value.trim() ? {} : { whatsapp: false, sms: false }) }); }} slotProps={{ htmlInput: { inputMode: "tel" } }} disabled={busy} />
              </Box>
              <Stack direction="row" sx={{ flexWrap: "wrap", alignItems: "center", gap: 1 }}>
                <FormControlLabel control={<Radio checked={primary === i} onChange={() => { setPrimary(i); }} />} label="Primary contact" />
                <FormControlLabel control={<Switch checked={c.whatsapp} disabled={busy || !c.phone.trim()} onChange={(e) => { updateContact(i, { whatsapp: e.target.checked }); }} />} label="Agrees to WhatsApp" />
                <FormControlLabel control={<Switch checked={c.sms} disabled={busy || !c.phone.trim()} onChange={(e) => { updateContact(i, { sms: e.target.checked }); }} />} label="Agrees to SMS" />
                {contacts.length > 1 && (
                  <Button color="error" size="small" sx={{ ml: "auto" }} disabled={busy}
                    onClick={() => { setContacts((list) => list.filter((_, j) => j !== i)); setPrimary((p) => (p === i ? 0 : p > i ? p - 1 : p)); }}>
                    Remove contact {i + 1}
                  </Button>
                )}
              </Stack>
            </Stack>
          </Paper>
        ))}
        <Box>
          <Button disabled={busy || contacts.length >= 10} onClick={() => { setContacts((list) => [...list, emptyContact()]); }}>Add contact</Button>
        </Box>
        <Typography variant="body2" color="text.secondary">
          Alerts go to the primary contact by email. WhatsApp and SMS are used only for contacts who have agreed.
        </Typography>
        <Box>
          <Button type="submit" variant="contained" size="large" disabled={busy || !ready}>{busy ? "Saving…" : "Save client"}</Button>
        </Box>
      </Stack>
    </Box>
  );
}

export function ClientPage() {
  const { clientId } = useParams();
  const location = useLocation();
  const justCreated = (location.state as { created?: boolean } | null)?.created === true;
  const [client, setClient] = useState<Client | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pan, setPan] = useState<string | null>(null);
  const [consentError, setConsentError] = useState<string | null>(null);

  const [history, setHistory] = useState<HistoryEntry[] | null>(null);
  const me = useUser();
  const [round, setRound] = useState(0);
  const [changed, setChanged] = useState<string | null>(null);
  const [gate, setGate] = useState<number | null>(null);

  useEffect(() => {
    const id = encodeURIComponent(clientId ?? "");
    api<Client>(`/api/clients/${id}`)
      .then((c) => { setGate(null); setClient(c); })
      .catch((e: unknown) => {
        const asked = reasonRequiredFor(e);
        if (asked === null) setError(messageFor(e));
        else setGate(asked);
      });
    api<HistoryEntry[]>(`/api/clients/${id}/history`).then(setHistory).catch(() => undefined);
  }, [clientId, round]);

  async function setConsent(contact: Contact, channel: "whatsapp" | "sms", granted: boolean) {
    if (!client) return;
    setConsentError(null);
    try {
      const updated = await api<Contact>(`/api/clients/${String(client.client_id)}/contacts/${String(contact.id)}/consent`, { method: "PUT", json: { channel, granted } });
      setClient({ ...client, contacts: client.contacts.map((c) => (c.id === updated.id ? updated : c)) });
    } catch (e) {
      setConsentError(messageFor(e));
    }
  }

  if (gate !== null) return <BreakGlass clientId={gate} onOpened={() => { setRound((r) => r + 1); }} />;
  if (error) return <Alert severity="error">{error}</Alert>;
  if (!client) return <Typography color="text.secondary">Loading…</Typography>;
  return (
    <Box sx={{ maxWidth: 720 }}>
      <Button component={RouterLink} to="/clients" size="small" sx={{ mb: 1, ml: -1 }}>← Clients</Button>
      {justCreated && <Alert severity="success" sx={{ mb: 2 }}>Client saved. Its client ID is {client.client_id}.</Alert>}
      <Typography variant="overline" color="text.secondary" component="p">Client ID</Typography>
      <Typography component="p" sx={{ fontSize: "2rem", fontWeight: 700, letterSpacing: "0.08em", mb: 1 }} data-testid="client-id">{client.client_id}</Typography>
      <Typography variant="h1" component="h1" sx={{ mb: 2, fontSize: { xs: "1.75rem", md: "2.25rem" } }}>{client.legal_name}</Typography>
      <Box component="dl" sx={{ display: "grid", gridTemplateColumns: "max-content 1fr", columnGap: 3, rowGap: 1, mb: 3, "& dt": { color: "text.secondary" }, "& dd": { m: 0 } }}>
        <dt>Type</dt><dd>{KIND_LABEL[client.kind]}</dd>
        <dt>PAN</dt>
        <dd>
          {client.pan_masked ? (
            <>
              <span data-testid="pan">{pan ?? client.pan_masked}</span>
              <Button
                size="small"
                sx={{ ml: 1 }}
                onClick={() => {
                  if (pan) { setPan(null); return; }
                  api<{ pan: string | null }>(`/api/clients/${String(client.client_id)}/pan`, { method: "POST" })
                    .then((r) => { setPan(r.pan); })
                    .catch((e: unknown) => { setConsentError(messageFor(e)); });
                }}
              >
                {pan ? "Hide" : "Show"}
              </Button>
            </>
          ) : "Not given"}
        </dd>
        {client.kind === "business" && <><dt>GSTIN</dt><dd>{client.gstin ?? "Not registered"}</dd></>}
        <dt>Owner</dt><dd>{client.owner.full_name}</dd>
        <dt>Package</dt><dd>{client.package.name} (version {client.package.version})</dd>
        <dt>Status</dt><dd>{client.status[0]?.toUpperCase()}{client.status.slice(1)}</dd>
      </Box>
      <Typography variant="h5" component="h2" sx={{ mb: 1 }}>Contacts</Typography>
      {consentError && <Alert severity="error" sx={{ mb: 2 }}>{consentError}</Alert>}
      <Stack spacing={2}>
        {client.contacts.map((c) => (
          <Paper key={c.id} variant="outlined" sx={{ p: 2, borderRadius: 3 }}>
            <Typography sx={{ fontWeight: 600 }}>{c.name}{c.is_primary ? " · primary contact" : ""}</Typography>
            <Typography color="text.secondary">{c.email}{c.phone ? ` · ${c.phone}` : ""}</Typography>
            <Stack direction="row" sx={{ flexWrap: "wrap", mt: 1 }}>
              <FormControlLabel control={<Switch checked disabled />} label="Email (always)" />
              <FormControlLabel control={<Switch checked={c.consent.whatsapp} disabled={!c.phone} onChange={(e) => void setConsent(c, "whatsapp", e.target.checked)} />} label={`WhatsApp for ${c.name}`} />
              <FormControlLabel control={<Switch checked={c.consent.sms} disabled={!c.phone} onChange={(e) => void setConsent(c, "sms", e.target.checked)} />} label={`SMS for ${c.name}`} />
            </Stack>
          </Paper>
        ))}
      </Stack>

      <ClientTickets clientId={client.client_id} />
      <ClientFees key={`fees-${String(round)}`} clientId={client.client_id} />
      <ClientFilings key={`filings-${String(round)}`} clientId={client.client_id} isAdmin={me.role === "administrator"} />

      <Typography variant="h5" component="h2" sx={{ mt: 4, mb: 1 }}>Change owner, package or status</Typography>
      {changed && <Alert severity="success" sx={{ mb: 2 }} onClose={() => { setChanged(null); }}>{changed}</Alert>}
      <ChangeForm key={round} client={client} onChanged={(text) => { setChanged(text); setRound((r) => r + 1); }} />

      <Box sx={{ mt: 4 }}>
        <ClientUpdates key={round} source={`/api/clients/${String(client.client_id)}/updates`} />
      </Box>

      <Typography variant="h5" component="h2" sx={{ mt: 4, mb: 1 }}>History</Typography>
      {history?.length === 0 && <Typography color="text.secondary">No changes since the client was added.</Typography>}
      {history && history.length > 0 && (
        <List aria-label="History" disablePadding>
          {history.map((h) => (
            <ListItemText
              key={h.id}
              sx={{ py: 1 }}
              primary={`${FIELD_LABEL[h.field]}: ${h.old} → ${h.new}`}
              secondary={`${formatIST(h.changed_at)} · ${h.changed_by.full_name} · “${h.reason}”`}
            />
          ))}
        </List>
      )}
    </Box>
  );
}

interface HistoryEntry {
  id: number;
  field: "owner" | "package" | "status" | "overdue_limit";
  old: string;
  new: string;
  reason: string;
  changed_by: { id: number; full_name: string };
  changed_at: string;
}

const FIELD_LABEL: Record<HistoryEntry["field"], string> = { owner: "Owner", package: "Package", status: "Status", overdue_limit: "Overdue limit" };

function ChangeForm({ client, onChanged }: { client: Client; onChanged: (text: string) => void }) {
  const me = useUser();
  const isAdmin = me.role === "administrator";
  const [options, setOptions] = useState<Options | null>(null);
  const [owner, setOwner] = useState(String(client.owner.id));
  const [pkg, setPkg] = useState(String(client.package.id));
  const [status, setStatus] = useState<Status>(client.status);
  const [reason, setReason] = useState("");
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Options>("/api/clients/options").then(setOptions).catch(() => undefined);
  }, []);

  const current = options?.packages.find((p) => p.id === client.package.id);
  const newerVersion = current !== undefined && current.version > client.package.version;
  const body: Record<string, unknown> = { reason: reason.trim() };
  if (owner !== String(client.owner.id)) body.owner_id = Number(owner);
  if (pkg !== String(client.package.id)) body.package_id = Number(pkg);
  if (status !== client.status) body.status = status;
  const anything = Object.keys(body).length > 1;
  const closedAndStaff = client.status === "closed" && !isAdmin;

  async function save(event: SyntheticEvent, extra: Record<string, unknown> = {}) {
    event.preventDefault();
    setBusy(true);
    setProblems([]);
    try {
      await api(`/api/clients/${String(client.client_id)}`, { method: "PATCH", json: { ...body, ...extra } });
      onChanged("Saved, and added to the history.");
    } catch (e) {
      setProblems(problemsFrom(e));
      setBusy(false);
    }
  }

  return (
    <Box component="form" onSubmit={(e) => void save(e)} noValidate>
      {problems.length > 0 && <Alert severity="error" sx={{ mb: 2 }}>{problems.join(" ")}</Alert>}
      {closedAndStaff && <Alert severity="info" sx={{ mb: 2 }}>This client is closed. Only the Administrator can reopen it.</Alert>}
      <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr 1fr" } }}>
        <TextField select label="New owner" value={owner} onChange={(e) => { setOwner(e.target.value); }} disabled={busy || !isAdmin}
          helperText={isAdmin ? " " : "The Administrator reassigns clients"}>
          <MenuItem value={String(client.owner.id)}>{client.owner.full_name}</MenuItem>
          {(options?.owners ?? []).filter((o) => o.id !== client.owner.id).map((o) => <MenuItem key={o.id} value={String(o.id)}>{o.full_name}</MenuItem>)}
        </TextField>
        <TextField select label="New package" value={pkg} onChange={(e) => { setPkg(e.target.value); }} disabled={busy || closedAndStaff}
          helperText={newerVersion ? `Version ${String(current.version)} of ${current.name} is available` : " "}>
          <MenuItem value={String(client.package.id)}>{client.package.name}</MenuItem>
          {(options?.packages ?? []).filter((p) => p.id !== client.package.id).map((p) => <MenuItem key={p.id} value={String(p.id)}>{p.name}</MenuItem>)}
        </TextField>
        <TextField select label="New status" value={status} onChange={(e) => { setStatus(e.target.value as Status); }} disabled={busy || closedAndStaff} helperText=" ">
          {(Object.keys(STATUS_LABEL) as Status[]).map((k) => <MenuItem key={k} value={k}>{STATUS_LABEL[k]}</MenuItem>)}
        </TextField>
      </Box>
      <TextField label="Reason" value={reason} onChange={(e) => { setReason(e.target.value); }} fullWidth multiline minRows={2} sx={{ mt: 1 }} disabled={busy}
        helperText="Required. Kept in the history with your name and the time." />
      <Stack direction="row" sx={{ mt: 2, gap: 1, flexWrap: "wrap" }}>
        <Button type="submit" variant="contained" disabled={busy || !anything || reason.trim().length < 3}>Save change</Button>
        {newerVersion && pkg === String(client.package.id) && (
          <Button variant="outlined" disabled={busy || reason.trim().length < 3} onClick={(e) => void save(e, { package_id: client.package.id })}>
            Move to version {current.version}
          </Button>
        )}
      </Stack>
    </Box>
  );
}

interface TicketRow {
  code: string;
  type: "filing" | "task";
  title: string;
  stage: string | null;
  stage_label: string | null;
  due_date: string | null;
}

function AddTask({ clientId }: { clientId: number }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [people, setPeople] = useState<{ id: number; full_name: string }[]>([]);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [assignee, setAssignee] = useState("");
  const [due, setDue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) api<{ id: number; full_name: string }[]>("/api/tickets/assignees").then(setPeople).catch(() => undefined);
  }, [open]);

  async function save(event: SyntheticEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const made = await api<{ code: string }>("/api/tickets", {
        method: "POST",
        json: { client_id: clientId, title: title.trim(), description: description.trim() || null, assignee_id: assignee ? Number(assignee) : null, due_date: due || null },
      });
      void navigate(`/tickets/${made.code}`);
    } catch (e) {
      setError(messageFor(e));
      setBusy(false);
    }
  }

  if (!open) return <Button onClick={() => { setOpen(true); }}>Add task</Button>;
  return (
    <Paper component="form" onSubmit={(e: SyntheticEvent) => void save(e)} noValidate variant="outlined" sx={{ p: 2, borderRadius: 3, mb: 2 }}>
      <Typography variant="subtitle1" component="h3" sx={{ fontWeight: 600, mb: 2 }}>New task</Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <Stack spacing={2}>
        <TextField label="What needs doing" value={title} onChange={(e) => { setTitle(e.target.value); }} required autoFocus disabled={busy} />
        <TextField label="Details" value={description} onChange={(e) => { setDescription(e.target.value); }} multiline minRows={2} disabled={busy} />
        <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" } }}>
          <TextField select label="Assign to" value={assignee} onChange={(e) => { setAssignee(e.target.value); }} disabled={busy}>
            <MenuItem value="">Nobody yet</MenuItem>
            {people.map((p) => <MenuItem key={p.id} value={String(p.id)}>{p.full_name}</MenuItem>)}
          </TextField>
          <TextField label="Due" type="date" value={due} onChange={(e) => { setDue(e.target.value); }} slotProps={{ inputLabel: { shrink: true } }} disabled={busy} />
        </Box>
        <Stack direction="row" sx={{ gap: 1 }}>
          <Button type="submit" variant="contained" disabled={busy || !title.trim()}>Create task</Button>
          <Button disabled={busy} onClick={() => { setOpen(false); }}>Cancel</Button>
        </Stack>
      </Stack>
    </Paper>
  );
}

function ClientTickets({ clientId }: { clientId: number }) {
  const [rows, setRows] = useState<TicketRow[] | null>(null);
  useEffect(() => {
    api<TicketRow[]>(`/api/clients/${String(clientId)}/tickets`).then(setRows).catch(() => { setRows([]); });
  }, [clientId]);
  return (
    <>
      <Stack direction="row" sx={{ mt: 4, mb: 1, alignItems: "center", justifyContent: "space-between" }}>
        <Typography variant="h5" component="h2">Tickets</Typography>
      </Stack>
      <AddTask clientId={clientId} />
      {rows?.length === 0 && <Typography color="text.secondary">No tickets yet. Filing cycles appear here once they are scheduled.</Typography>}
      {rows && rows.length > 0 && (
        <List aria-label="Tickets" disablePadding sx={{ border: 1, borderColor: "divider", borderRadius: 3 }}>
          {rows.map((t, i) => (
            <ListItemButton key={t.code} component={RouterLink} to={`/tickets/${t.code}`} divider={i < rows.length - 1}>
              <ListItemText primary={`${t.code} · ${t.title}`} secondary={[t.stage_label, t.due_date ? `due ${formatDay(t.due_date)}` : null].filter(Boolean).join(" · ")} />
            </ListItemButton>
          ))}
        </List>
      )}
    </>
  );
}
