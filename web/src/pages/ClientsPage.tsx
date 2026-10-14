import { useEffect, useState, type SyntheticEvent } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import FormControlLabel from "@mui/material/FormControlLabel";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Radio from "@mui/material/Radio";
import RadioGroup from "@mui/material/RadioGroup";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { Link as RouterLink, useLocation, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, messageFor } from "../api";
import { useUser } from "../auth/AuthProvider";
import { BILLING_LABEL, money, type BillingFrequency } from "../catalog";

type Kind = "individual" | "business";

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
  pan: string | null;
  gstin: string | null;
  status: "active" | "paused" | "closed";
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
export function maskPan(pan: string): string {
  return `${pan.slice(0, 2)}•••••${pan.slice(7)}`;
}

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

export function ClientsHome() {
  const navigate = useNavigate();
  const [id, setId] = useState("");
  const valid = /^\d{6}$/.test(id.trim());
  return (
    <>
      <Stack direction="row" sx={{ alignItems: "center", justifyContent: "space-between", mb: 2 }}>
        <Typography variant="h1" component="h1" sx={{ display: { xs: "none", md: "block" } }}>Clients</Typography>
        <Button variant="contained" component={RouterLink} to="/clients/new" sx={{ ml: "auto" }}>Add client</Button>
      </Stack>
      <Typography color="text.secondary" sx={{ mb: 2, maxWidth: 560 }}>
        The client list and search come next. Until then, open a client by its 6-digit client ID.
      </Typography>
      <Box component="form" onSubmit={(e) => { e.preventDefault(); if (valid) void navigate(`/clients/${id.trim()}`); }} sx={{ display: "flex", gap: 1, maxWidth: 400 }}>
        <TextField label="Client ID" value={id} onChange={(e) => { setId(e.target.value); }} slotProps={{ htmlInput: { inputMode: "numeric", maxLength: 6 } }} sx={{ flex: 1 }} />
        <Button type="submit" variant="outlined" disabled={!valid}>Open</Button>
      </Box>
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
  const [showPan, setShowPan] = useState(false);
  const [consentError, setConsentError] = useState<string | null>(null);

  useEffect(() => {
    api<Client>(`/api/clients/${encodeURIComponent(clientId ?? "")}`)
      .then(setClient)
      .catch((e: unknown) => { setError(messageFor(e)); });
  }, [clientId]);

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
          {client.pan ? (
            <>
              <span data-testid="pan">{showPan ? client.pan : maskPan(client.pan)}</span>
              <Button size="small" sx={{ ml: 1 }} onClick={() => { setShowPan((s) => !s); }}>{showPan ? "Hide" : "Show"}</Button>
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
    </Box>
  );
}
