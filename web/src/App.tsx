import CircularProgress from "@mui/material/CircularProgress";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";
import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth, useUser, type Role } from "./auth/AuthProvider";
import { AppShell } from "./components/AppShell";
import { AccountSecurityPage } from "./pages/AccountSecurityPage";
import { AuditPage } from "./pages/AuditPage";
import { AssignmentPage } from "./consultant/MyWork";
import { ClientPage, ClientsHome, NewClientPage } from "./pages/ClientsPage";
import { ChangePasswordPage } from "./pages/ChangePasswordPage";
import { EmptyPage } from "./pages/EmptyPage";
import { HomePage } from "./pages/HomePage";
import { LoginPage } from "./pages/LoginPage";
import { FilingPage, FilingsPage } from "./pages/FilingsPage";
import { HolidaysPage } from "./pages/HolidaysPage";
import { EmailPage } from "./pages/EmailPage";
import { ExtensionsPage } from "./pages/ExtensionsPage";
import { ReportsPage } from "./pages/ReportsPage";
import { RulesPage } from "./pages/RulesPage";
import { FeesSetupPage } from "./pages/FeesSetupPage";
import { NotificationsPage } from "./pages/NotificationsPage";
import { PackagePage, PackagesPage } from "./pages/PackagesPage";
import { SetupPage } from "./pages/SetupPage";
import { TeamPage } from "./pages/TeamPage";
import { TicketPage } from "./pages/TicketPage";
import { TicketsPage } from "./pages/TicketsPage";

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
        <Route path="notifications" element={<OnlyFor roles={["administrator", "staff"]}><NotificationsPage /></OnlyFor>} />
        <Route path="assignments/:assignmentId" element={<OnlyFor roles={["consultant"]}><AssignmentPage /></OnlyFor>} />
        <Route path="work" element={<OnlyFor roles={["administrator", "staff"]}><TicketsPage /></OnlyFor>} />
        <Route path="reports" element={<OnlyFor roles={["administrator", "staff"]}><ReportsPage /></OnlyFor>} />
        <Route path="clients" element={<OnlyFor roles={["administrator", "staff"]}><ClientsHome /></OnlyFor>} />
        <Route path="clients/new" element={<OnlyFor roles={["administrator", "staff"]}><NewClientPage /></OnlyFor>} />
        <Route path="tickets/:ticketId" element={<OnlyFor roles={["administrator", "staff"]}><TicketPage /></OnlyFor>} />
        <Route path="clients/:clientId" element={<OnlyFor roles={["administrator", "staff"]}><ClientPage /></OnlyFor>} />
        <Route path="team" element={<Navigate to="/setup/team" replace />} />
        <Route path="setup" element={<OnlyFor roles={["administrator"]}><SetupPage /></OnlyFor>} />
        <Route path="setup/team" element={<OnlyFor roles={["administrator"]}><TeamPage /></OnlyFor>} />
        <Route path="setup/filings" element={<OnlyFor roles={["administrator"]}><FilingsPage /></OnlyFor>} />
        <Route path="setup/filings/:filingId" element={<OnlyFor roles={["administrator"]}><FilingPage /></OnlyFor>} />
        <Route path="setup/holidays" element={<OnlyFor roles={["administrator"]}><HolidaysPage /></OnlyFor>} />
        <Route path="setup/email" element={<OnlyFor roles={["administrator"]}><EmailPage /></OnlyFor>} />
        <Route path="setup/extensions" element={<OnlyFor roles={["administrator"]}><ExtensionsPage /></OnlyFor>} />
        <Route path="setup/rules" element={<OnlyFor roles={["administrator"]}><RulesPage /></OnlyFor>} />
        <Route path="setup/fees" element={<OnlyFor roles={["administrator"]}><FeesSetupPage /></OnlyFor>} />
        <Route path="setup/packages" element={<OnlyFor roles={["administrator"]}><PackagesPage /></OnlyFor>} />
        <Route path="setup/packages/:packageId" element={<OnlyFor roles={["administrator"]}><PackagePage /></OnlyFor>} />
        <Route path="audit" element={<OnlyFor roles={["administrator"]}><AuditPage /></OnlyFor>} />
        <Route path="account" element={<AccountSecurityPage />} />
        <Route path="*" element={<EmptyPage title="Page not found">Check the address, or use the menu to go back.</EmptyPage>} />
      </Route>
    </Routes>
  );
}
