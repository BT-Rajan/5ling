import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { admin, jsonResponse, renderApp, staff, stubScreen } from "../test-utils";

const quiet = { "GET /api/health/ready": () => new Promise<Response>(() => undefined), "GET /api/admin/audit/status": () => jsonResponse({ latest: null }), "GET /api/worklist": () => new Promise<Response>(() => undefined), "GET /api/notifications": () => jsonResponse({ items: [], unread: 0 }) };

const dash = {
  on: "2026-10-14",
  owner_id: null,
  stages: [{ stage: "scheduled", label: "Scheduled", count: 30 }, { stage: "assigned", label: "Assigned", count: 1 }],
  overdue_filings: 2,
  documents_missing: 0,
  workload: [{ id: 9, full_name: "Ravi Consultant", role: "consultant", count: 1 }],
  fees_due: { count: 2, amount: "2260.00" },
  fees_overdue: { count: 1, amount: "1180.00" },
};

beforeEach(() => {
  stubScreen(true);
});

describe("Dashboard", () => {
  it("shows the figures, and each opens the list behind it", async () => {
    const asked: string[] = [];
    renderApp("/", {
      user: admin,
      handlers: {
        ...quiet,
        "GET /api/dashboard": () => jsonResponse(dash),
        "GET /api/dashboard/list?figure=overdue_filings": (c) => {
          asked.push(c.path);
          return jsonResponse({ figure: "overdue_filings", key: null, total: 2, cycles: [
            { code: "TKT-000001", client_id: 100001, client_name: "Asha Rao", filing: "GSTR-1", period_label: "Sep 2026", due_date: "2026-10-11", stage: "awaiting_documents", stage_label: "Awaiting documents" },
            { code: "TKT-000002", client_id: 100002, client_name: "Meena", filing: "GSTR-1", period_label: "Sep 2026", due_date: "2026-10-11", stage: "preparing", stage_label: "Preparing" },
          ], invoices: [] });
        },
        "GET /api/dashboard/list?figure=fees_overdue": () => jsonResponse({ figure: "fees_overdue", key: null, total: 1, cycles: [], invoices: [{ invoice_no: "INV/26-27/00001", client_id: 100002, client_name: "Meena", period_label: "Sep 2026", due_date: "2026-09-11", total: "1180.00", balance: "1180.00" }] }),
        "GET /api/dashboard/list?figure=workload&key=9": () => jsonResponse({ figure: "workload", key: "9", total: 0, cycles: [], invoices: [] }),
      },
    });
    expect(await screen.findByRole("heading", { name: "The whole practice" })).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "Overdue filings: 2" }));
    const region = await screen.findByRole("region", { name: "Overdue filings" });
    expect(await within(region).findByRole("heading", { name: "Overdue filings (2)" })).toBeInTheDocument();
    expect(within(region).getAllByRole("link")).toHaveLength(2);
    expect(within(region).getByRole("link", { name: /Asha Rao · GSTR-1 Sep 2026/ })).toHaveAttribute("href", "/tickets/TKT-000001");

    await userEvent.click(screen.getByRole("button", { name: "Fees overdue: 1, ₹1,180" }));
    const fees = await screen.findByRole("region", { name: "Fees overdue" });
    expect(await within(fees).findByText(/₹1,180 of ₹1,180 owed/)).toBeInTheDocument();
    await userEvent.click(within(screen.getByRole("list", { name: "Workload" })).getByRole("button", { name: /Ravi Consultant/ }));
    expect(await screen.findByText("Nothing here.")).toBeInTheDocument();
    expect(asked).toEqual(["/api/dashboard/list?figure=overdue_filings"]);
  });

  it("tells Staff it is their own clients", async () => {
    renderApp("/", { user: staff, handlers: { ...quiet, "GET /api/dashboard": () => jsonResponse({ ...dash, owner_id: 2 }) } });
    expect(await screen.findByRole("heading", { name: "Your clients" })).toBeInTheDocument();
  });
});
