import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import FormControlLabel from "@mui/material/FormControlLabel";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import type { ChecklistEntry } from "../catalog";

/** The documents a client is asked for. Each row: what it is, and whether it is required. */
export function ChecklistEditor({
  items,
  onChange,
  label,
  disabled = false,
}: {
  items: ChecklistEntry[];
  onChange: (items: ChecklistEntry[]) => void;
  label: string;
  disabled?: boolean;
}) {
  function update(index: number, change: Partial<ChecklistEntry>) {
    onChange(items.map((item, i) => (i === index ? { ...item, ...change } : item)));
  }
  return (
    <Box component="fieldset" sx={{ border: 0, p: 0, m: 0 }}>
      <Typography component="legend" variant="subtitle2" sx={{ mb: 1 }}>
        {label}
      </Typography>
      <Stack spacing={1}>
        {items.length === 0 && <Typography color="text.secondary">No documents asked for.</Typography>}
        {items.map((item, i) => (
          <Stack key={i} direction="row" spacing={1} sx={{ alignItems: "center" }}>
            <TextField
              size="small"
              label={`Document ${i + 1}`}
              value={item.label}
              onChange={(e) => { update(i, { label: e.target.value }); }}
              disabled={disabled}
              sx={{ flex: 1 }}
            />
            <FormControlLabel
              control={<Switch checked={item.required} onChange={(e) => { update(i, { required: e.target.checked }); }} disabled={disabled} />}
              label="Required"
            />
            <IconButton
              aria-label={`Remove document ${i + 1}`}
              onClick={() => { onChange(items.filter((_, j) => j !== i)); }}
              disabled={disabled}
            >
              <span aria-hidden="true">×</span>
            </IconButton>
          </Stack>
        ))}
      </Stack>
      <Button size="small" sx={{ mt: 1 }} disabled={disabled || items.length >= 30} onClick={() => { onChange([...items, { label: "", required: true }]); }}>
        Add document
      </Button>
    </Box>
  );
}

/** Drop empty rows before sending. */
export function cleanChecklist(items: ChecklistEntry[]): ChecklistEntry[] {
  return items.map((i) => ({ ...i, label: i.label.trim() })).filter((i) => i.label !== "");
}
