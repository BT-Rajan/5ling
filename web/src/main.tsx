import "@fontsource-variable/roboto-flex/wght.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import CssBaseline from "@mui/material/CssBaseline";
import { ThemeProvider } from "@mui/material/styles";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { ConsultantSignIn, takeToken } from "./consultant/ConsultantSignIn";
import { PortalApp } from "./portal/PortalApp";
import { theme } from "./theme";

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element");

// The client portal is a separate little app: no staff sign-in, no staff session check.
const isPortal = window.location.pathname === "/portal" || window.location.pathname.startsWith("/portal/");
// A consultant's emailed sign-in link: no session yet, so no session check either.
const isConsultantLink = window.location.pathname === "/consultant/sign-in";

createRoot(root).render(
  <StrictMode>
    <ThemeProvider theme={theme} defaultMode="system">
      <CssBaseline enableColorScheme />
      {isPortal ? (
        <PortalApp />
      ) : isConsultantLink ? (
        <ConsultantSignIn token={takeToken()} />
      ) : (
        <AuthProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </AuthProvider>
      )}
    </ThemeProvider>
  </StrictMode>,
);

if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    void navigator.serviceWorker.register("/sw.js");
  });
}
