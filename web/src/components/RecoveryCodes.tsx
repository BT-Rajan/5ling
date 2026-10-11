import { useState } from "react";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

/** The one time recovery codes are shown. Offers copy and download so they can be kept safely. */
export function RecoveryCodes({ codes }: { codes: readonly string[] }) {
  const [copied, setCopied] = useState(false);
  const text = codes.join("\n");

  function download() {
    const url = URL.createObjectURL(new Blob([`Ledgerline recovery codes\nEach works once.\n\n${text}\n`], { type: "text/plain" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "ledgerline-recovery-codes.txt";
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <Stack spacing={2}>
      <Box
        component="ul"
        aria-label="Recovery codes"
        sx={{
          m: 0,
          p: 2,
          listStyle: "none",
          display: "grid",
          gridTemplateColumns: "repeat(2, 1fr)",
          gap: 1,
          borderRadius: 2,
          bgcolor: "primary.light",
          color: "primary.dark",
          fontFamily: "ui-monospace, monospace",
          fontSize: "1rem",
          textAlign: "center",
          userSelect: "all",
        }}
      >
        {codes.map((code) => (
          <li key={code}>{code}</li>
        ))}
      </Box>
      <Stack direction="row" spacing={1}>
        {"clipboard" in navigator && (
          <Button
            variant="outlined"
            onClick={() => {
              void navigator.clipboard.writeText(text).then(() => {
                setCopied(true);
              });
            }}
          >
            {copied ? "Copied" : "Copy all"}
          </Button>
        )}
        <Button variant="outlined" onClick={download}>
          Download
        </Button>
      </Stack>
      <Typography variant="body2" color="text.secondary">
        Keep them somewhere safe and separate from your phone, such as a password manager.
      </Typography>
    </Stack>
  );
}
