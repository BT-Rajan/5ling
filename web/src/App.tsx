import CircularProgress from "@mui/material/CircularProgress";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";
import type { ReactNode } from "react";
import { Route, Routes } from "react-router-dom";
import { useAuth, useUser, type Role } from "./auth/AuthProvider";
import { AppShell } from "./components/AppShell";
import { ChangePasswordPage } from "./pages/ChangePasswordPage";
import { EmptyPage } from "./pages/EmptyPage";
import { HomePage } from "./pages/HomePage";
import { LoginPage } from "./pages/LoginPage";
import { TeamPage } from "./pages/TeamPage";

/** Hides a screen from people who should not see it. The server enforces the same rule on every request. */
function OnlyFor({ roles, children }: { roles: readonly Role[]; children: ReactNode }) {
  const user = useUser();
  if (!roles.includes(user.role)) {
    return <EmptyPage title="No access">You do not have access to this page.</EmptyPage>;
  }
  return children;
}

function Centered({ children }: { children: ReactNode }) {
  return (
    <Box sx={{ minHeight: "100dvh", display: "grid", placeItems: "center", p: 3, textAlign: "center", gap: 2 }}>
      <Box>{children}</Box>
    </Box>
  );
}

export function App() {
  const { state, retry } = useAuth();

  if (state.status === "loading") {
    return (
      <Centered>
        <CircularProgress aria-label="Loading" />
      </Centered>
    );
  }
  if (state.status === "unavailable") {
    return (
      <Centered>
        <Typography variant="h2" component="h1" sx={{ mb: 1 }}>We could not reach Ledgerline</Typography>
        <Typography color="text.secondary" sx={{ mb: 3 }}>Check your connection, then try again.</Typography>
        <Button variant="contained" onClick={retry}>Try again</Button>
      </Centered>
    );
  }
  if (state.status === "anonymous") return <LoginPage />;
  if (state.user.must_change_password) return <ChangePasswordPage forced />;

  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<HomePage />} />
        <Route path="work" element={<EmptyPage title="Work list">Nothing to do yet. Your daily work list will appear here once filings are set up.</EmptyPage>} />
        <Route path="clients" element={<EmptyPage title="Clients">No clients yet. Add your first client once client records are available.</EmptyPage>} />
        <Route path="team" element={<OnlyFor roles={["administrator"]}><TeamPage /></OnlyFor>} />
        <Route path="account/password" element={<ChangePasswordPage />} />
        <Route path="*" element={<EmptyPage title="Page not found">Check the address, or use the menu to go back.</EmptyPage>} />
      </Route>
    </Routes>
  );
}
