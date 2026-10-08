import { createTheme } from "@mui/material/styles";
import { tokens as t } from "./tokens";

// Material 3 conventions (tonal surfaces, pill indicators, 48px touch targets, Roboto Flex)
// so the app feels at home on Android and on the web. Colours follow the system light/dark setting.
const scheme = (c: (typeof t)["light"] | (typeof t)["dark"]) => ({
  palette: {
    primary: { main: c.primary, light: c.primaryContainer, dark: c.onPrimaryContainer, contrastText: c.onPrimary },
    secondary: { main: c.secondary },
    // "warning" is the brass tone: filings that are due soon.
    warning: { main: c.brass, light: c.brassContainer, dark: c.onBrassContainer, contrastText: c.onPrimary },
    error: { main: c.error },
    success: { main: c.success },
    background: { default: c.surface, paper: c.card },
    text: { primary: c.text, secondary: c.textMuted },
    divider: c.outline,
  },
});

export const theme = createTheme({
  cssVariables: { colorSchemeSelector: "media" },
  colorSchemes: { light: scheme(t.light), dark: scheme(t.dark) },
  shape: { borderRadius: 12 },
  typography: {
    fontFamily: '"Roboto Flex Variable", Roboto, system-ui, -apple-system, "Segoe UI", sans-serif',
    h1: { fontSize: "2rem", lineHeight: 1.25, fontWeight: 400, letterSpacing: 0 },
    h2: { fontSize: "1.375rem", lineHeight: 1.27, fontWeight: 500, letterSpacing: 0 },
    h3: { fontSize: "1rem", lineHeight: 1.5, fontWeight: 500, letterSpacing: "0.009em" },
    body1: { fontSize: "1rem", lineHeight: 1.5, letterSpacing: "0.03em" },
    body2: { fontSize: "0.875rem", lineHeight: 1.43, letterSpacing: "0.016em" },
    button: { fontSize: "0.875rem", fontWeight: 500, letterSpacing: "0.007em", textTransform: "none" },
    caption: { fontSize: "0.75rem", lineHeight: 1.33, fontWeight: 500, letterSpacing: "0.033em" },
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: `
        html { -webkit-text-size-adjust: 100%; }
        body {
          font-variant-numeric: tabular-nums; /* ledger figures line up */
          -webkit-tap-highlight-color: transparent;
          touch-action: manipulation;
        }
        :focus-visible { outline: 3px solid var(--mui-palette-primary-main); outline-offset: 2px; }
        @media (prefers-reduced-motion: reduce) {
          *, *::before, *::after { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }
        }
      `,
    },
    MuiButton: {
      styleOverrides: { root: { borderRadius: 20, minHeight: 48, paddingInline: 24 } },
    },
    MuiPaper: { styleOverrides: { root: { backgroundImage: "none" } } },
  },
});
