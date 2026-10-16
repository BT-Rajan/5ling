import type { ReactNode } from "react";
import { ThemeProvider } from "@mui/material/styles";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { ClientFilings, FilingChangesQueue } from "../components/ClientFilings";
import { admin, jsonResponse, mockApi, renderApp, staff, stubScreen, type Call } from "../test-utils";
import { theme } from "../theme";

const quiet = { "GET /api/health/ready": () => new Promise<Response>(() => undefined), "GET /api/admin/audit/status": () => jsonResponse({ latest: null }) };

beforeEach(() => {
  stubScreen(true);
});

function show(node: ReactNode) {
  return render(<ThemeProvider theme={theme}><MemoryRouter>{node}</MemoryRouter></ThemeProvider>);
}

const gst = { id: 1, code: "GSTR-1", name: "Outward supplies" };
const tds = { id: 2, code: "TDS-26Q", name: "TDS return" };
const filings = (removalPending: boolean) => ({
  filings: [{ ...gst, from_package: true, removal_pending: removalPending }],
  available: [tds],
  changes: removalPending
    ? [{ id: 9, client_id: 100001, client_name: "Asha Rao", filing: gst, kind: "remove", status: "pending", reason: "Client closed GST", requested_by: "Sita Staff", requested_at: "2026-10-14T05:00:00Z", decided_by: null, decided_at: null, decision_note: null }]
    : [],
});

describe("A client's own filings (FR-PK-07)", () => {
  it("lets Staff ask to remove a filing, which then waits for approval", async () => {
    const posts: Call[] = [];
    mockApi({
      user: staff,
      handlers: {
        "GET /api/clients/100001/filings": () => jsonResponse(filings(false)),
        "POST /api/clients/100001/filings": (c) => { posts.push(c); return jsonResponse(filings(true), 201); },
      },
    });
    show(<ClientFilings clientId={100001} isAdmin={false} />);
    const list = await screen.findByRole("list", { name: "This client's filings" });
    await userEvent.click(within(list).getByRole("button", { name: "Remove" }));
    expect(screen.getByText("An Administrator approves removals. The client keeps the filing until then.")).toBeInTheDocument();
    const ask = screen.getByRole("button", { name: "Ask to remove" });
    expect(ask).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Why remove GSTR-1?"), "Client closed GST");
    await userEvent.click(ask);
    expect(await screen.findByText("Removal waiting for approval")).toBeInTheDocument();
    expect(screen.getByText("Remove GSTR-1: waiting for an Administrator")).toBeInTheDocument();
    expect(posts[0]?.body).toEqual({ filing_id: 1, action: "remove", reason: "Client closed GST" });
  });

  it("shows Administrators the waiting removals to approve or refuse with a note", async () => {
    const decisions: Call[] = [];
    let waiting = [filings(true).changes[0]];
    mockApi({
      user: admin,
      handlers: {
        "GET /api/admin/filing-changes": () => jsonResponse(waiting),
        "POST /api/admin/filing-changes/9/decision": (c) => {
          decisions.push(c);
          waiting = [];
          return jsonResponse({ ...filings(true).changes[0], status: "approved", withdrawn: ["TKT-000002", "TKT-000003"] });
        },
      },
    });
    show(<FilingChangesQueue />);
    const item = await screen.findByRole("article", { name: "Asha Rao: remove GSTR-1" });
    expect(within(item).getByRole("button", { name: "Refuse" })).toBeDisabled();
    await userEvent.click(within(item).getByRole("button", { name: "Approve" }));
    expect(await screen.findByText("GSTR-1 removed for Asha Rao; 2 unstarted cycles closed.")).toBeInTheDocument();
    expect(decisions[0]?.body).toEqual({ approve: true, note: null });
  });
});

describe("Access review (FR-AC-04)", () => {
  it("lists access with when it was generated, and exports it", async () => {
    const exports: Call[] = [];
    renderApp("/setup/access-review", {
      user: admin,
      handlers: {
        ...quiet,
        "GET /api/admin/access-review": () =>
          jsonResponse({
            generated_at: "2026-10-16T04:30:00Z",
            generated_by: "The Boss",
            rows: [
              { client_id: 100001, client_name: "Asha Rao", client_status: "active", person: "Sita Staff", email: "sita@firm.in", role: "Staff", access: "Owner", since: "2026-04-01T05:00:00Z" },
              { client_id: null, client_name: "All clients", client_status: null, person: "The Boss", email: "boss@firm.in", role: "Administrator", access: "Administrator: every client", since: null },
            ],
          }),
        "POST /api/admin/access-review/export": (c) => {
          exports.push(c);
          return new Response("x", { status: 200, headers: { "Content-Type": "application/pdf", "Content-Disposition": 'attachment; filename="ledgerline-access-review-2026-10-16-1000.pdf"' } });
        },
      },
    });
    expect(await screen.findByTestId("generated")).toHaveTextContent("Generated");
    const table = screen.getByRole("table", { name: "Access to clients" });
    expect(within(table).getByText("100001 · Asha Rao")).toBeInTheDocument();
    expect(within(table).getByText("All clients")).toBeInTheDocument();
    URL.createObjectURL = () => "blob:x";
    URL.revokeObjectURL = () => undefined;
    await userEvent.click(screen.getByRole("button", { name: "Export PDF" }));
    expect(exports[0]?.body).toEqual({ format: "pdf" });
  });

  it("is listed in Settings for Administrators", async () => {
    renderApp("/setup", { user: admin, handlers: quiet });
    expect(await screen.findByRole("link", { name: /Access review/ })).toHaveAttribute("href", "/setup/access-review");
  });
});
