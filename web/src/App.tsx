import { Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { EmptyPage } from "./pages/EmptyPage";
import { HomePage } from "./pages/HomePage";

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<HomePage />} />
        <Route path="work" element={<EmptyPage title="Work list">Nothing to do yet. Your daily work list will appear here once filings are set up.</EmptyPage>} />
        <Route path="clients" element={<EmptyPage title="Clients">No clients yet. Add your first client once client records are available.</EmptyPage>} />
        <Route path="*" element={<EmptyPage title="Page not found">Check the address, or use the menu to go back.</EmptyPage>} />
      </Route>
    </Routes>
  );
}
