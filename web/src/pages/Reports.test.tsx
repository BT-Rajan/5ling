import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { admin, jsonResponse, renderApp, staff, stubScreen, type Call } from "../test-utils";

const quiet = { "GET /api/health/ready": () => new Promise<Response>(() => undefined), "GET /api/admin/audit/status": () => jsonResponse({ latest: null }) };

const status = { report: "filing-status", title: "Filing status by period", start: "2026-08-01", end: "2026-11-14", owner_id: null, columns: ["Filing", "Period", "Due", "Cycles", "Not started", "In progress", "Filed on time", "Filed late", "Overdue"], rows: [["GSTR-1", "Sep 2026", "2026-10-11", 4, 1, 1, 1, 0, 1]], total: 1, truncated: false };
const aging = { report: "fee-aging", title: "Fee aging", start: "2026-08-01", end: "2026-11-14", owner_id: 2, columns: ["Client ID", "Client", "Owner", "Not yet due", "1-30 days", "31-60 days", "61-90 days", "Over 90 days", "Total owed"], rows: [], total: 0, truncated: false };

beforeEach(() => {
  stubScreen(true);
});

describe("Reports", () => {
  it("shows a report and downloads it as a spreadsheet", async () => {
    const posts: Call[] = [];
    const createObjectURL = vi.fn(() => "blob:report");
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }));
    const clicked: HTMLAnchorElement[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { clicked.push(this); });
    renderApp("/reports", {
      user: admin,
      handlers: {
        ...quiet,
        "GET /api/reports/filing-status": () => jsonResponse(status),
        "GET /api/reports/filing-status?from=2026-09-01": () => jsonResponse({ ...status, start: "2026-09-01" }),
        "POST /api/reports/filing-status/export": (c) => {
          posts.push(c);
          return new Response("PK", { headers: { "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "Content-Disposition": 'attachment; filename="ledgerline-filing-status-2026-09-01-to-2026-11-14.xlsx"' } });
        },
      },
    });
    const table = await screen.findByRole("table", { name: "Filing status by period" });
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    expect(table).toHaveTextContent("GSTR-1Sep 20262026-10-1141110");
    await userEvent.type(screen.getByLabelText("From"), "2026-09-01");
    expect(await screen.findByText(/From 2026-09-01 to 2026-11-14/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Download spreadsheet" }));
    await waitFor(() => { expect(clicked).toHaveLength(1); });
    expect(posts[0]?.body).toEqual({ format: "xlsx", from: "2026-09-01" });
    expect(posts[0]?.headers.get("X-CSRF-Token")).toBe("csrf-1");
    expect(clicked[0]?.download).toBe("ledgerline-filing-status-2026-09-01-to-2026-11-14.xlsx");
    click.mockRestore();
  });

  it("tells Staff the reports cover their own clients", async () => {
    renderApp("/reports", {
      user: staff,
      handlers: { ...quiet, "GET /api/reports/filing-status": () => jsonResponse({ ...status, owner_id: 2 }), "GET /api/reports/fee-aging": () => jsonResponse(aging) },
    });
    expect(await screen.findByText(/Your clients only/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("combobox", { name: "Report" }));
    await userEvent.click(await screen.findByRole("option", { name: "Fee aging" }));
    expect(await screen.findByText("Nothing to show for this range.")).toBeInTheDocument();
    expect(screen.getByLabelText("From")).toBeDisabled();
  });
});
