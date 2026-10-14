import { useCallback, useEffect, useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { api, messageFor } from "../api";
import { formatIST } from "../time";

interface Link {
  id: number;
  status: "waiting" | "opened" | "ended" | "expired" | "replaced" | "withdrawn";
  status_label: string;
  origin: "staff" | "client_request" | "system";
  created_by: string | null;
  created_at: string;
  expires_at: string;
  opened_at: string | null;
  last_seen_at: string | null;
}

interface Made {
  link: Link;
  url: string;
  emailed_to: string | null;
}

const ORIGIN = { staff: "Made by", client_request: "Asked for by the client", system: "Sent with an alert" } as const;

/** The client's private link for this cycle (BRD FR-AL-02): make, email, copy or withdraw it. */
export function ClientLinks({ code }: { code: string }) {
  const [links, setLinks] = useState<Link[] | null>(null);
  const [made, setMade] = useState<Made | null>(null);
  const [notice, setNotice] = useState<{ severity: "success" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api<Link[]>(`/api/tickets/${code}/links`)
      .then(setLinks)
      .catch((e: unknown) => { setNotice({ severity: "error", text: messageFor(e) }); });
  }, [code]);
  useEffect(load, [load]);

  async function make(sendEmail: boolean) {
    setBusy(true);
    setNotice(null);
    setMade(null);
    try {
      const result = await api<Made>(`/api/tickets/${code}/links`, { method: "POST", json: { send_email: sendEmail } });
      if (result.emailed_to) setNotice({ severity: "success", text: `A new link is on its way to ${result.emailed_to}. Earlier unopened links no longer work.` });
      else setMade(result);
      load();
    } catch (e) {
      setNotice({ severity: "error", text: messageFor(e) });
    } finally {
      setBusy(false);
    }
  }

  async function withdraw(link: Link) {
    setBusy(true);
    setNotice(null);
    try {
      await api(`/api/tickets/${code}/links/${String(link.id)}/withdraw`, { method: "POST" });
      setNotice({ severity: "success", text: "Withdrawn. It stops working at once." });
      setMade(null);
      load();
    } catch (e) {
      setNotice({ severity: "error", text: messageFor(e) });
    } finally {
      setBusy(false);
    }
  }

  async function copy(url: string) {
    try {
      await navigator.clipboard.writeText(url);
      setNotice({ severity: "success", text: "Copied." });
    } catch {
      setNotice({ severity: "error", text: "Could not copy. Select the link and copy it by hand." });
    }
  }

  return (
    <Box component="section" aria-labelledby="links-heading" sx={{ mb: 3 }}>
      <Typography id="links-heading" variant="h5" component="h2" sx={{ mb: 1 }}>Client link</Typography>
      <Typography color="text.secondary" sx={{ mb: 2 }}>
        A private link for this filing only. It opens once, on one device, and stops working after its expiry. A new link stops earlier unopened ones.
      </Typography>
      {notice && <Alert severity={notice.severity} sx={{ mb: 2 }}>{notice.text}</Alert>}
      <Stack direction="row" sx={{ gap: 1, flexWrap: "wrap", mb: 2 }}>
        <Button variant="outlined" disabled={busy} onClick={() => void make(true)}>Email a new link to the client</Button>
        <Button disabled={busy} onClick={() => void make(false)}>Make a link to copy</Button>
      </Stack>
      {made && (
        <Paper variant="outlined" sx={{ p: 2, borderRadius: 3, mb: 2 }}>
          <Typography sx={{ mb: 1 }}>Send this to the client yourself. It is shown only now.</Typography>
          <Box sx={{ display: "grid", gap: 1, gridTemplateColumns: { xs: "1fr", sm: "1fr auto" }, alignItems: "center" }}>
            <TextField value={made.url} label="Private link" slotProps={{ htmlInput: { readOnly: true } }} onFocus={(e) => { e.target.select(); }} />
            <Button variant="contained" onClick={() => void copy(made.url)}>Copy</Button>
          </Box>
        </Paper>
      )}
      {links && links.length > 0 && (
        <List aria-label="Client links" dense disablePadding sx={{ border: 1, borderColor: "divider", borderRadius: 3 }}>
          {links.map((l, i) => (
            <ListItem
              key={l.id}
              divider={i < links.length - 1}
              secondaryAction={(l.status === "waiting" || l.status === "opened") && (
                <Button size="small" color="error" disabled={busy} onClick={() => void withdraw(l)}>Withdraw</Button>
              )}
            >
              <ListItemText
                primary={l.status_label}
                secondary={[
                  l.origin === "staff" ? `${ORIGIN.staff} ${l.created_by ?? "someone"}` : ORIGIN[l.origin],
                  formatIST(l.created_at),
                  l.opened_at ? `opened ${formatIST(l.opened_at)}` : `until ${formatIST(l.expires_at)}`,
                ].join(" · ")}
              />
            </ListItem>
          ))}
        </List>
      )}
    </Box>
  );
}
