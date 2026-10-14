import { useCallback, useEffect, useRef, useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { api, messageFor, uploadFile } from "../api";
import { formatIST } from "../time";

interface Item {
  id: number;
  label: string;
  required: boolean;
  status: "pending" | "checking" | "received";
  status_label: string;
  files: { version: number; filename: string; uploaded_at: string; checked: boolean }[];
}

export interface ChecklistData {
  items: Item[];
  still_needed: number;
  allowed: string;
  max_mb: number;
}

const COLOR = { pending: "warning", checking: "info", received: "success" } as const;

function ItemCard({ item, busy, onFile, problem }: { item: Item; busy: boolean; onFile: (file: File) => void; problem: string | null }) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <Paper component="section" variant="outlined" sx={{ p: 2, borderRadius: 3 }} aria-label={item.label}>
      <Stack direction="row" sx={{ alignItems: "center", gap: 1, flexWrap: "wrap", mb: 1 }}>
        <Typography component="h3" sx={{ fontWeight: 600, mr: "auto", overflowWrap: "anywhere" }}>
          {item.label}{!item.required && <Typography component="span" color="text.secondary"> (if you have it)</Typography>}
        </Typography>
        <Chip size="small" color={COLOR[item.status]} label={item.status_label} />
      </Stack>
      {item.files.map((f) => (
        <Typography key={f.version} variant="body2" color="text.secondary" sx={{ overflowWrap: "anywhere" }}>
          {f.filename} · {formatIST(f.uploaded_at)}{f.checked ? "" : " · being checked"}
        </Typography>
      ))}
      {problem && <Alert severity="error" sx={{ mt: 1 }} role="alert">{problem}</Alert>}
      <input
        ref={input}
        type="file"
        hidden
        aria-label={`Upload ${item.label}`}
        accept=".pdf,.jpg,.jpeg,.png,.xlsx,.xls,.docx,.csv,.txt"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) onFile(file);
        }}
      />
      <Button sx={{ mt: 1.5 }} fullWidth variant={item.status === "pending" ? "contained" : "outlined"} disabled={busy} onClick={() => input.current?.click()}>
        {item.status === "pending" ? "Upload" : "Upload a newer copy"}
      </Button>
    </Paper>
  );
}

/** FR-AL-04: what is still needed, one upload button per item, and why a file was refused. */
export function Checklist({ csrf }: { csrf: string }) {
  const [data, setData] = useState<ChecklistData | null>(null);
  const [busyItem, setBusyItem] = useState<number | null>(null);
  const [problems, setProblems] = useState<Record<number, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const load = useCallback(() => {
    api<ChecklistData>("/api/portal/checklist")
      .then(setData)
      .catch((e: unknown) => { setError(messageFor(e)); });
  }, []);
  useEffect(load, [load]);

  async function send(item: Item, file: File) {
    setBusyItem(item.id);
    setDone(null);
    setProblems((p) => Object.fromEntries(Object.entries(p).filter(([id]) => id !== String(item.id))));
    try {
      setData(await uploadFile<ChecklistData>(`/api/uploads/portal/items/${String(item.id)}`, file, { "X-CSRF-Token": csrf }));
      setDone(`${file.name} received for ${item.label}. Thank you.`);
    } catch (e) {
      setProblems((p) => ({ ...p, [item.id]: messageFor(e) }));
    } finally {
      setBusyItem(null);
    }
  }

  if (error) return <Alert severity="error" sx={{ mb: 3 }}>{error}</Alert>;
  if (!data) return null;
  if (data.items.length === 0) return <Typography color="text.secondary" sx={{ mb: 3 }}>We do not need any documents from you for this filing.</Typography>;
  return (
    <Box component="section" aria-labelledby="checklist-heading" sx={{ mb: 3 }}>
      <Typography id="checklist-heading" variant="h2" component="h2" sx={{ fontSize: "1.25rem", mb: 1 }}>Documents we need</Typography>
      <Typography sx={{ mb: 1 }} data-testid="still-needed">
        {data.still_needed === 0 ? "We have everything we need. Thank you." : `${String(data.still_needed)} still needed.`}
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{data.allowed} files, up to {data.max_mb} MB each.</Typography>
      {done && <Alert severity="success" sx={{ mb: 2 }}>{done}</Alert>}
      <Stack spacing={2}>
        {data.items.map((item) => (
          <ItemCard key={item.id} item={item} busy={busyItem !== null} problem={problems[item.id] ?? null} onFile={(f) => void send(item, f)} />
        ))}
      </Stack>
    </Box>
  );
}
