import { useCallback, useEffect, useRef, useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { api, messageFor, uploadFile } from "../api";
import { formatIST } from "../time";

interface Version {
  id: number;
  version: number;
  filename: string;
  content_type: string;
  size_bytes: number;
  uploaded_via: "staff" | "portal";
  uploaded_by: string | null;
  uploaded_at: string;
  scan_status: "pending" | "clean" | "infected";
  scan_label: string;
}

export interface DocumentItem {
  id: number;
  title: string;
  latest_version: number;
  versions: Version[];
}

interface ChecklistItem {
  id: number;
  label: string;
  required: boolean;
  status: "pending" | "checking" | "received";
  status_label: string;
  document_id: number | null;
}

const ITEM_COLOR = { pending: "warning", checking: "info", received: "success" } as const;

const ACCEPT = ".pdf,.jpg,.jpeg,.png,.xlsx,.xls,.docx,.csv,.txt";

function size(bytes: number): string {
  if (bytes < 1024) return `${String(bytes)} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** A hidden file input behind a button: picking a file uploads it at once. */
function PickFile({ label, onFile, disabled, variant = "outlined" }: { label: string; onFile: (file: File) => void; disabled: boolean; variant?: "outlined" | "text" | "contained" }) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={input}
        type="file"
        accept={ACCEPT}
        hidden
        aria-label={label}
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) onFile(file);
        }}
      />
      <Button size="small" variant={variant} disabled={disabled} onClick={() => input.current?.click()}>{label}</Button>
    </>
  );
}

/** FR-AL-05: each document with its versions; nothing is ever overwritten. */
export function Documents({ code }: { code: string }) {
  const [docs, setDocs] = useState<DocumentItem[] | null>(null);
  const [items, setItems] = useState<ChecklistItem[]>([]);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ severity: "success" | "error" | "info"; text: string } | null>(null);

  const load = useCallback(() => {
    api<DocumentItem[]>(`/api/tickets/${code}/documents`)
      .then(setDocs)
      .catch((e: unknown) => { setNotice({ severity: "error", text: messageFor(e) }); });
    api<ChecklistItem[]>(`/api/tickets/${code}/checklist`)
      .then(setItems)
      .catch(() => undefined);
  }, [code]);
  useEffect(load, [load]);

  async function send(file: File, documentId?: number, itemId?: number) {
    setBusy(true);
    setNotice(null);
    const params = new URLSearchParams();
    if (itemId !== undefined) params.set("item_id", String(itemId));
    else if (documentId !== undefined) params.set("document_id", String(documentId));
    else if (title.trim()) params.set("title", title.trim());
    try {
      const result = await uploadFile<DocumentItem[]>(`/api/uploads/tickets/${code}?${params.toString()}`, file);
      setDocs(result);
      setTitle("");
      if (itemId !== undefined) load();
      const added = result.flatMap((d) => d.versions).reduce((a, b) => (a.id > b.id ? a : b));
      setNotice(added.scan_status === "pending"
        ? { severity: "info", text: `${file.name} is stored and will be available once the virus check has run.` }
        : { severity: "success", text: `${file.name} uploaded.` });
    } catch (e) {
      setNotice({ severity: "error", text: messageFor(e) });
    } finally {
      setBusy(false);
    }
  }

  async function open(version: Version) {
    setNotice(null);
    try {
      const link = await api<{ url: string }>(`/api/documents/versions/${String(version.id)}/link`, { method: "POST" });
      window.location.assign(link.url);
    } catch (e) {
      setNotice({ severity: "error", text: messageFor(e) });
    }
  }

  return (
    <Box component="section" aria-labelledby="documents-heading" sx={{ mb: 3 }}>
      <Typography id="documents-heading" variant="h5" component="h2" sx={{ mb: 1 }}>Documents</Typography>
      <Typography color="text.secondary" sx={{ mb: 2 }}>Every file is checked for viruses. A new upload adds a version; earlier versions stay.</Typography>
      {notice && <Alert severity={notice.severity} sx={{ mb: 2 }}>{notice.text}</Alert>}
      {items.length > 0 && (
        <List aria-label="Checklist" dense disablePadding sx={{ border: 1, borderColor: "divider", borderRadius: 3, mb: 2 }}>
          {items.map((item, i) => (
            <ListItem
              key={item.id}
              divider={i < items.length - 1}
              secondaryAction={<PickFile label={`Upload for ${item.label}`} variant="text" disabled={busy} onFile={(f) => void send(f, undefined, item.id)} />}
            >
              <ListItemText
                primary={<Box component="span" sx={{ display: "flex", gap: 1, alignItems: "center", flexWrap: "wrap" }}><span>{item.label}{item.required ? "" : " (optional)"}</span><Chip size="small" variant="outlined" color={ITEM_COLOR[item.status]} label={item.status_label} /></Box>}
                sx={{ pr: 18 }}
              />
            </ListItem>
          ))}
        </List>
      )}
      <Box sx={{ display: "grid", gap: 1, gridTemplateColumns: { xs: "1fr", sm: "1fr auto" }, alignItems: "center", mb: 2 }}>
        <TextField size="small" label="Document name (optional)" value={title} onChange={(e) => { setTitle(e.target.value); }} slotProps={{ htmlInput: { maxLength: 200 } }} disabled={busy} />
        <PickFile label="Upload a document" variant="contained" disabled={busy} onFile={(f) => void send(f)} />
      </Box>
      {docs?.length === 0 && <Typography color="text.secondary">No documents yet.</Typography>}
      <Stack spacing={2}>
        {docs?.map((d) => (
          <Paper key={d.id} variant="outlined" sx={{ borderRadius: 3 }}>
            <Stack direction="row" sx={{ alignItems: "center", gap: 1, px: 2, pt: 1.5, flexWrap: "wrap" }}>
              <Typography sx={{ fontWeight: 600, mr: "auto", overflowWrap: "anywhere" }}>{d.title}</Typography>
              <PickFile label="Upload a new version" disabled={busy} onFile={(f) => void send(f, d.id)} />
            </Stack>
            <List aria-label={`Versions of ${d.title}`} dense>
              {d.versions.map((v) => (
                <ListItem
                  key={v.id}
                  secondaryAction={v.scan_status === "clean" && <Button size="small" onClick={() => void open(v)}>Download</Button>}
                >
                  <ListItemText
                    primary={<Box component="span" sx={{ display: "flex", gap: 1, alignItems: "center", flexWrap: "wrap" }}><span>Version {v.version} · {v.filename}</span>{v.scan_status !== "clean" && <Chip size="small" variant="outlined" color={v.scan_status === "infected" ? "error" : "warning"} label={v.scan_label} />}</Box>}
                    secondary={`${v.uploaded_by ?? "The client"} · ${formatIST(v.uploaded_at)} · ${size(v.size_bytes)}`}
                    sx={{ pr: 12, overflowWrap: "anywhere" }}
                  />
                </ListItem>
              ))}
            </List>
          </Paper>
        ))}
      </Stack>
    </Box>
  );
}
