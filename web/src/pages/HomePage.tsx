import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Paper from "@mui/material/Paper";
import Typography from "@mui/material/Typography";
import { useHealth, type DatabaseState, type ServiceState } from "../hooks/useHealth";

type Tone = "success" | "error" | "warning" | "neutral";

const TONE: Record<Tone, { bg: string; fg: string }> = {
  success: { bg: "primary.light", fg: "primary.dark" },
  error: { bg: "transparent", fg: "error.main" },
  warning: { bg: "warning.light", fg: "warning.dark" },
  neutral: { bg: "transparent", fg: "text.secondary" },
};

function serviceLabel(s: ServiceState): [string, Tone] {
  if (s === "up") return ["Running", "success"];
  if (s === "down") return ["Not reachable", "error"];
  return ["Checking", "neutral"];
}

function databaseLabel(s: DatabaseState): [string, Tone] {
  switch (s) {
    case "ok":
      return ["Connected", "success"];
    case "needs_setup":
      return ["Needs setup", "warning"];
    case "unavailable":
      return ["Unavailable", "error"];
    case "unknown":
      return ["Not checked", "neutral"];
    default:
      return ["Checking", "neutral"];
  }
}

function Row({ name, value }: { name: string; value: [string, Tone] }) {
  const [text, tone] = value;
  return (
    <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", py: 2 }}>
      <Typography>{name}</Typography>
      <Box
        sx={{
          px: 1.5,
          py: 0.5,
          borderRadius: 2,
          bgcolor: TONE[tone].bg,
          color: TONE[tone].fg,
          border: tone === "error" || tone === "neutral" ? 1 : 0,
          borderColor: "divider",
        }}
      >
        <Typography variant="caption" sx={{ color: "inherit" }}>
          {text}
        </Typography>
      </Box>
    </Box>
  );
}

export function HomePage() {
  const { service, database, refresh } = useHealth();
  return (
    <>
      <Typography variant="h1" component="h1" sx={{ mb: 3, display: { xs: "none", md: "block" } }}>
        Home
      </Typography>
      <Paper variant="outlined" sx={{ p: { xs: 2, md: 3 }, maxWidth: 560 }}>
        <Typography variant="h2" component="h2">
          System status
        </Typography>
        <Box sx={{ mt: 1, "& > * + *": { borderTop: 1, borderColor: "divider" } }}>
          <Row name="Service" value={serviceLabel(service)} />
          <Row name="Database" value={databaseLabel(database)} />
        </Box>
        <Button variant="outlined" onClick={refresh} sx={{ mt: 2 }}>
          Check again
        </Button>
      </Paper>
      <Typography color="text.secondary" sx={{ mt: 3, maxWidth: 560 }}>
        Filings, clients and tickets will appear here as they are set up.
      </Typography>
    </>
  );
}
