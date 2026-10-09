import { useCallback, useEffect, useState, type SyntheticEvent } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogContentText from "@mui/material/DialogContentText";
import DialogTitle from "@mui/material/DialogTitle";
import IconButton from "@mui/material/IconButton";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";
import { api, messageFor } from "../api";
import { useUser, type Role } from "../auth/AuthProvider";

interface Person {
  id: number;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  must_change_password: boolean;
  agreement_signed_on: string | null;
}

interface Created {
  person: Person;
  temporary_password: string | null;
}

const ROLE_LABEL: Record<Role, string> = {
  administrator: "Administrator",
  staff: "Staff",
  consultant: "Consultant",
};

type Confirm = { kind: "disable" | "enable" | "reset"; person: Person };
type Secret = { heading: string; person: Person; password: string | null };

function statusText(p: Person): string {
  if (!p.is_active) return "Disabled";
  if (p.must_change_password) return "Has not signed in yet";
  return "Active";
}

function useFullScreen(): boolean {
  const theme = useTheme();
  return !useMediaQuery(theme.breakpoints.up("sm"), { noSsr: true });
}

function AddPersonDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (c: Created) => void }) {
  const fullScreen = useFullScreen();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("staff");
  const [agreement, setAgreement] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const consultant = role === "consultant";
  const ready = name.trim() !== "" && email.trim() !== "" && (!consultant || agreement !== "");

  async function submit(event: SyntheticEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api<Created>("/api/admin/users", {
        method: "POST",
        json: {
          email: email.trim(),
          full_name: name.trim(),
          role,
          agreement_signed_on: consultant ? agreement : null,
        },
      });
      onCreated(created);
    } catch (e) {
      setError(messageFor(e));
      setBusy(false);
    }
  }

  return (
    <Dialog open onClose={busy ? undefined : onClose} fullScreen={fullScreen} fullWidth maxWidth="xs" aria-labelledby="add-person-title">
      <form onSubmit={(e) => void submit(e)} noValidate>
        <DialogTitle id="add-person-title">Add a person</DialogTitle>
        <DialogContent>
          <Stack spacing={2.5} sx={{ pt: 1 }}>
            {error && <Alert severity="error">{error}</Alert>}
            <TextField label="Full name" value={name} onChange={(e) => { setName(e.target.value); }} required autoFocus fullWidth />
            <TextField
              label="Email"
              type="email"
              value={email}
              onChange={(e) => { setEmail(e.target.value); }}
              slotProps={{ htmlInput: { inputMode: "email", autoCapitalize: "none" } }}
              required
              fullWidth
            />
            <TextField
              select
              label="Role"
              value={role}
              onChange={(e) => { setRole(e.target.value as Role); }}
              helperText={consultant ? "Consultants sign in with an emailed link and see only the task given to them." : " "}
              fullWidth
            >
              <MenuItem value="staff">Staff</MenuItem>
              <MenuItem value="consultant">Consultant</MenuItem>
              <MenuItem value="administrator">Administrator</MenuItem>
            </TextField>
            {consultant && (
              <TextField
                label="Confidentiality agreement signed on"
                type="date"
                value={agreement}
                onChange={(e) => { setAgreement(e.target.value); }}
                slotProps={{ inputLabel: { shrink: true } }}
                required
                fullWidth
              />
            )}
          </Stack>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={onClose} disabled={busy}>Cancel</Button>
          <Button type="submit" variant="contained" disabled={busy || !ready}>
            {busy ? "Adding…" : "Add person"}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}

function SecretDialog({ secret, onClose }: { secret: Secret; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const { person, password } = secret;
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="xs" aria-labelledby="secret-title">
      <DialogTitle id="secret-title">{secret.heading}</DialogTitle>
      <DialogContent>
        {password ? (
          <>
            <DialogContentText sx={{ mb: 2 }}>
              Give this temporary password to {person.full_name} in person or by phone. It is shown only once, and
              they must choose their own at first sign-in.
            </DialogContentText>
            <Box
              sx={{ p: 2, borderRadius: 2, bgcolor: "primary.light", color: "primary.dark", fontFamily: "ui-monospace, monospace", fontSize: "1.25rem", textAlign: "center", userSelect: "all" }}
              data-testid="temporary-password"
            >
              {password}
            </Box>
          </>
        ) : (
          <DialogContentText>
            {person.full_name} has been added. Consultants sign in with an emailed link, which is not available yet,
            so they cannot sign in for now.
          </DialogContentText>
        )}
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        {password && "clipboard" in navigator && (
          <Button
            onClick={() => {
              void navigator.clipboard.writeText(password).then(() => { setCopied(true); });
            }}
          >
            {copied ? "Copied" : "Copy"}
          </Button>
        )}
        <Button variant="contained" onClick={onClose}>Done</Button>
      </DialogActions>
    </Dialog>
  );
}

function ConfirmDialog({ confirm, onCancel, onConfirm, busy, error }: {
  confirm: Confirm; onCancel: () => void; onConfirm: () => void; busy: boolean; error: string | null;
}) {
  const name = confirm.person.full_name;
  const text = {
    disable: [`Disable ${name}?`, "They are signed out straight away and cannot sign in until you enable them again.", "Disable"],
    enable: [`Enable ${name}?`, "They will be able to sign in again.", "Enable"],
    reset: [`Reset password for ${name}?`, "They are signed out, and need a new temporary password to get back in.", "Reset password"],
  }[confirm.kind];
  return (
    <Dialog open onClose={busy ? undefined : onCancel} aria-labelledby="confirm-title">
      <DialogTitle id="confirm-title">{text[0]}</DialogTitle>
      <DialogContent>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        <DialogContentText>{text[1]}</DialogContentText>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onCancel} disabled={busy}>Cancel</Button>
        <Button variant="contained" color={confirm.kind === "disable" ? "error" : "primary"} onClick={onConfirm} disabled={busy}>
          {text[2]}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export function TeamPage() {
  const me = useUser();
  const [people, setPeople] = useState<Person[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [menu, setMenu] = useState<{ anchor: HTMLElement; person: Person } | null>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [secret, setSecret] = useState<Secret | null>(null);

  const load = useCallback(() => {
    api<Person[]>("/api/admin/users")
      .then((list) => {
        setPeople(list);
        setLoadError(null);
      })
      .catch((e: unknown) => {
        setLoadError(messageFor(e));
      });
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  async function run() {
    if (!confirm) return;
    setBusy(true);
    setConfirmError(null);
    try {
      const { kind, person } = confirm;
      if (kind === "reset") {
        const out = await api<{ temporary_password: string }>(`/api/admin/users/${person.id}/reset-password`, { method: "POST" });
        setSecret({ heading: "New temporary password", person, password: out.temporary_password });
      } else {
        await api(`/api/admin/users/${person.id}/${kind}`, { method: "POST" });
      }
      setConfirm(null);
      load();
    } catch (e) {
      setConfirmError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Stack direction="row" sx={{ alignItems: "center", justifyContent: "space-between", mb: 2 }}>
        <Typography variant="h1" component="h1" sx={{ display: { xs: "none", md: "block" } }}>Team</Typography>
        <Button variant="contained" onClick={() => { setAdding(true); }} sx={{ ml: "auto" }}>Add person</Button>
      </Stack>

      {loadError && <Alert severity="error" action={<Button color="inherit" onClick={load}>Try again</Button>}>{loadError}</Alert>}
      {people === null && !loadError && <Typography color="text.secondary">Loading…</Typography>}
      {people && (
        <List disablePadding sx={{ maxWidth: 720, border: 1, borderColor: "divider", borderRadius: 3 }}>
          {people.map((p, i) => (
            <ListItem
              key={p.id}
              divider={i < people.length - 1}
              secondaryAction={
                <IconButton
                  edge="end"
                  aria-label={`Actions for ${p.full_name}`}
                  onClick={(e) => { setMenu({ anchor: e.currentTarget, person: p }); }}
                  sx={{ width: 48, height: 48 }}
                >
                  <span aria-hidden="true" style={{ fontSize: 22, lineHeight: 1 }}>⋮</span>
                </IconButton>
              }
              sx={{ py: 1.5, opacity: p.is_active ? 1 : 0.7 }}
            >
              <ListItemText
                primary={`${p.full_name}${p.id === me.id ? " (you)" : ""}`}
                secondary={`${p.email} · ${ROLE_LABEL[p.role]} · ${statusText(p)}`}
                slotProps={{ primary: { sx: { fontWeight: 500 } } }}
              />
            </ListItem>
          ))}
        </List>
      )}

      <Menu anchorEl={menu?.anchor ?? null} open={menu !== null} onClose={() => { setMenu(null); }}>
        {menu && menu.person.is_active && menu.person.id !== me.id && (
          <MenuItem onClick={() => { setConfirm({ kind: "disable", person: menu.person }); setMenu(null); }}>Disable</MenuItem>
        )}
        {menu && !menu.person.is_active && (
          <MenuItem onClick={() => { setConfirm({ kind: "enable", person: menu.person }); setMenu(null); }}>Enable</MenuItem>
        )}
        {menu && menu.person.role !== "consultant" && menu.person.id !== me.id && (
          <MenuItem onClick={() => { setConfirm({ kind: "reset", person: menu.person }); setMenu(null); }}>Reset password</MenuItem>
        )}
        {menu && menu.person.id === me.id && <MenuItem disabled>No actions on your own account</MenuItem>}
      </Menu>

      {adding && (
        <AddPersonDialog
          onClose={() => { setAdding(false); }}
          onCreated={(c) => {
            setAdding(false);
            setSecret({ heading: "Person added", person: c.person, password: c.temporary_password });
            load();
          }}
        />
      )}
      {confirm && (
        <ConfirmDialog
          confirm={confirm}
          busy={busy}
          error={confirmError}
          onCancel={() => { setConfirm(null); setConfirmError(null); }}
          onConfirm={() => void run()}
        />
      )}
      {secret && <SecretDialog secret={secret} onClose={() => { setSecret(null); }} />}
    </>
  );
}
