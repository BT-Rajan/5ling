import type { ReactNode } from "react";
import Box from "@mui/material/Box";
import Paper from "@mui/material/Paper";
import Typography from "@mui/material/Typography";
import { LogoMark } from "../components/Icons";

/** Centered card used before someone is signed in (and when a password change is required). */
export function AuthLayout({ title, intro, children }: { title: string; intro?: string; children: ReactNode }) {
  return (
    <Box
      component="main"
      sx={{
        minHeight: "100dvh",
        display: "grid",
        placeItems: { xs: "start", sm: "center" },
        bgcolor: "background.default",
        p: { xs: 0, sm: 3 },
        pt: { xs: "env(safe-area-inset-top, 0px)", sm: 3 },
      }}
    >
      <Paper
        variant="outlined"
        sx={{ width: "100%", maxWidth: 440, p: { xs: 3, sm: 4 }, border: { xs: 0, sm: 1 }, borderRadius: { xs: 0, sm: 3 } }}
      >
        <Box sx={{ mb: 3 }}>
          <LogoMark size={44} />
        </Box>
        <Typography variant="h1" component="h1" sx={{ mb: 1 }}>
          {title}
        </Typography>
        {intro && (
          <Typography color="text.secondary" sx={{ mb: 3 }}>
            {intro}
          </Typography>
        )}
        {children}
      </Paper>
    </Box>
  );
}
