import { useCallback, useEffect, useRef, useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemText from "@mui/material/ListItemText";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { Link as RouterLink, useParams } from "react-router-dom";
import { api, messageFor, uploadFile } from "../api";
import type { DocumentItem } from "../components/Documents";
import { formatDay, formatIST } from "../time";

interface Work {
  id: number;
  ticket: string;
  client_name: string;
  filing: string;
  period_label: string;
  due_date: string;
  work_due_date: string | null;
  note: string | null;
  stage_label: string;
  assigned_by: string;
  assigned_at: string;
}

interface ReviewNote {
  action: "submit_for_review" | "return" | "approve";
  label: string;
  note: string | null;
  by: string | null;
  at: string;
}

interface WorkDetail extends Work {
  documents: DocumentItem[];
  allowed: string;
  max_mb: number;
  can_upload: boolean;
  can_submit: boolean;
  review: ReviewNote[];
}

const SAID = { submit_for_review: "Sent for review", return: "Returned to you", approve: "Approved" } as const;

/** FR-WF-02: a consultant's open assignments, and nothing else. */
export function MyWork() {
  const [work, setWork] = useState<Work[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api<Work[]>("/api/consultant/assignments").then(setWork).catch((e: unknown) => { setError(messageFor(e)); });
  }, []);
  return (
    <>
      <Typography variant="h1" component="h1" sx={{ mb: 2, fontSize: { xs: "1.5rem", md: "2rem" } }}>Your work</Typography>
      {error && <Alert severity="error">{error}</Alert>}
      {work?.length === 0 && <Typography color="text.secondary">Nothing is assigned to you right now.</Typography>}
      {work && work.length > 0 && (
        <List aria-label="Your work" disablePadding sx={{ border: 1, borderColor: "divider", borderRadius: 3 }}>
          {work.map((w, i) => (
            <ListItemButton key={w.id} component={RouterLink} to={`/assignments/${String(w.id)}`} divider={i < work.length - 1}>
              <ListItemText
                primary={`${w.client_name} · ${w.filing} ${w.period_label}`}
                secondary={[w.ticket, w.work_due_date ? `work due ${formatDay(w.work_due_date)}` : null, `filing due ${formatDay(w.due_date)}`].filter(Boolean).join(" · ")}
              />
            </ListItemButton>
          ))}
        </List>
      )}
    </>
  );
}

/** One assignment: its documents to download, and a place to upload the work. */
export function AssignmentPage() {
  const { assignmentId } = useParams();
  const [work, setWork] = useState<WorkDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ severity: "success" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    api<WorkDetail>(`/api/consultant/assignments/${encodeURIComponent(assignmentId ?? "")}`)
      .then(setWork)
      .catch((e: unknown) => { setError(messageFor(e)); });
  }, [assignmentId]);
  useEffect(load, [load]);

  async function open(versionId: number) {
    setNotice(null);
    try {
      const link = await api<{ url: string }>(`/api/documents/versions/${String(versionId)}/link`, { method: "POST" });
      window.location.assign(link.url);
    } catch (e) {
      setNotice({ severity: "error", text: messageFor(e) });
    }
  }

  async function send(file: File) {
    setBusy(true);
    setNotice(null);
    try {
      const docs = await uploadFile<DocumentItem[]>(`/api/uploads/consultant/assignments/${encodeURIComponent(assignmentId ?? "")}`, file);
      setWork((w) => (w ? { ...w, documents: docs } : w));
      load(); // the first upload moves it to Preparing
      setNotice({ severity: "success", text: `${file.name} uploaded.` });
    } catch (e) {
      setNotice({ severity: "error", text: messageFor(e) });
    } finally {
      setBusy(false);
    }
  }

  async function submit() {
    setBusy(true);
    setNotice(null);
    try {
      const text = note.trim();
      setWork(await api<WorkDetail>(`/api/consultant/assignments/${encodeURIComponent(assignmentId ?? "")}/submit`, { method: "POST", json: text ? { note: text } : {} }));
      setNote("");
      setNotice({ severity: "success", text: "Sent for review." });
    } catch (e) {
      setNotice({ severity: "error", text: messageFor(e) });
    } finally {
      setBusy(false);
    }
  }

  if (error) {
    return (
      <>
        <Alert severity="warning" sx={{ mb: 2 }}>{error}</Alert>
        <Button component={RouterLink} to="/">Your work</Button>
      </>
    );
  }
  if (!work) return <Typography color="text.secondary">Loading…</Typography>;
  return (
    <>
      <Button component={RouterLink} to="/" size="small" sx={{ mb: 1, ml: -1 }}>← Your work</Button>
      <Typography variant="overline" color="text.secondary" component="p">{work.ticket}</Typography>
      <Typography variant="h1" component="h1" sx={{ mb: 2, fontSize: { xs: "1.5rem", md: "2rem" } }}>{work.client_name} · {work.filing} {work.period_label}</Typography>
      <Paper variant="outlined" sx={{ p: 2, borderRadius: 3, mb: 3 }}>
        <Box component="dl" sx={{ display: "grid", gridTemplateColumns: "max-content 1fr", columnGap: 3, rowGap: 0.75, m: 0, "& dt": { color: "text.secondary" }, "& dd": { m: 0 } }}>
          <dt>Work due</dt><dd>{work.work_due_date ? formatDay(work.work_due_date) : "Not set"}</dd>
          <dt>Filing due</dt><dd>{formatDay(work.due_date)}</dd>
          <dt>Stage</dt><dd>{work.stage_label}</dd>
          <dt>Assigned by</dt><dd>{work.assigned_by}, {formatIST(work.assigned_at)}</dd>
          {work.note && <><dt>Note</dt><dd>{work.note}</dd></>}
        </Box>
      </Paper>
      {notice && <Alert severity={notice.severity} sx={{ mb: 2 }}>{notice.text}</Alert>}
      {work.review.length > 0 && (
        <Box component="section" aria-labelledby="review-heading" sx={{ mb: 3 }}>
          <Typography id="review-heading" variant="h5" component="h2" sx={{ mb: 1 }}>Review</Typography>
          <List dense aria-label="Review comments" disablePadding sx={{ border: 1, borderColor: "divider", borderRadius: 3 }}>
            {work.review.map((r, i) => (
              <ListItem key={`${r.action}-${r.at}`} divider={i < work.review.length - 1}>
                <ListItemText primary={`${SAID[r.action]}${r.note ? `: “${r.note}”` : ""}`} secondary={`${r.by ?? "System"} · ${formatIST(r.at)}`} />
              </ListItem>
            ))}
          </List>
        </Box>
      )}
      {work.can_submit && (
        <Paper variant="outlined" sx={{ p: 2, borderRadius: 3, mb: 3 }}>
          <TextField label="Note for the reviewer (optional)" value={note} onChange={(e) => { setNote(e.target.value); }} fullWidth multiline minRows={2} slotProps={{ htmlInput: { maxLength: 1000 } }} disabled={busy} />
          <Button variant="contained" sx={{ mt: 2 }} disabled={busy} onClick={() => void submit()}>Send for review</Button>
        </Paper>
      )}
      <Stack direction="row" sx={{ alignItems: "center", mb: 1, gap: 2, flexWrap: "wrap" }}>
        <Typography variant="h5" component="h2" sx={{ mr: "auto" }}>Documents</Typography>
        <input ref={input} type="file" hidden aria-label="Upload your work" onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) void send(f); }} />
        {work.can_upload && <Button variant="contained" disabled={busy} onClick={() => input.current?.click()}>Upload your work</Button>}
      </Stack>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {work.can_upload ? `${work.allowed} files, up to ${String(work.max_mb)} MB each.` : "This work is with the reviewer, so it cannot be changed now."}
      </Typography>
      {work.documents.length === 0 && <Typography color="text.secondary">No documents yet.</Typography>}
      <Stack spacing={2}>
        {work.documents.map((d) => (
          <Paper key={d.id} variant="outlined" sx={{ borderRadius: 3 }}>
            <Typography sx={{ fontWeight: 600, px: 2, pt: 1.5 }}>{d.title}</Typography>
            <List dense aria-label={`Versions of ${d.title}`}>
              {d.versions.map((v) => (
                <ListItem key={v.id} secondaryAction={v.scan_status === "clean" && <Button size="small" onClick={() => void open(v.id)}>Download</Button>}>
                  <ListItemText primary={`Version ${String(v.version)} · ${v.filename}`} secondary={`${v.uploaded_by ?? "The client"} · ${formatIST(v.uploaded_at)}${v.scan_status === "clean" ? "" : ` · ${v.scan_label}`}`} sx={{ pr: 12 }} />
                </ListItem>
              ))}
            </List>
          </Paper>
        ))}
      </Stack>
    </>
  );
}
