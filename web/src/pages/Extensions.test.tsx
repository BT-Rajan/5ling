import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { admin, errorResponse, jsonResponse, renderApp, stubScreen, type Call } from "../test-utils";

const quiet = { "GET /api/health/ready": () => new Promise<Response>(() => undefined), "GET /api/admin/audit/status": () => jsonResponse({ latest: null }) };

const filing = { id: 3, code: "GSTR-1", name: "Outward supplies", description: null, default_checklist: [], is_active: true, current: null, draft: null };
const period = { period_start: "2026-09-01", period_label: "Sep 2026", open_cycles: 2, earliest_due: "2026-10-12", latest_due: "2026-10-12" };
const preview = {
  filing_code: "GSTR-1",
  period_label: "Sep 2026",
  new_due_date: "2026-10-20",
  changes: 2,
  unchanged: 0,
  rows: [
    { code: "TKT-000004", client_id: 100001, client_name: "Asha Rao", stage: "scheduled", stage_label: "Scheduled", old_due_date: "2026-10-12", new_due_date: "2026-10-20", old_alert_date: "2026-10-08", new_alert_date: "2026-10-16" },
    { code: "TKT-000009", client_id: 100002, client_name: "Bina Shah", stage: "preparing", stage_label: "Preparing", old_due_date: "2026-10-12", new_due_date: "2026-10-20", old_alert_date: "2026-10-08", new_alert_date: "2026-10-16" },
  ],
};
const applied = { id: 1, filing_code: "GSTR-1", filing_name: "Outward supplies", period_start: "2026-09-01", period_label: "Sep 2026", new_due_date: "2026-10-20", reason: "Notification 12/2026", cycles_changed: 2, created_by: "Boss", created_at: "2026-10-14T05:00:00Z" };

beforeEach(() => {
  stubScreen(true);
});

async function choose(label: string, option: string) {
  await userEvent.click(screen.getByRole("combobox", { name: label }));
  await userEvent.click(await screen.findByRole("option", { name: option }));
}

describe("Due-date extensions", () => {
  it("previews the cycles that move, then applies with a reason", async () => {
    const posts: Call[] = [];
    let history: unknown[] = [];
    renderApp("/setup/extensions", {
      user: admin,
      handlers: {
        ...quiet,
        "GET /api/admin/filings": () => jsonResponse([filing]),
        "GET /api/admin/extensions": () => jsonResponse(history),
        "GET /api/admin/extensions/periods?filing_id=3": () => jsonResponse([period]),
        "POST /api/admin/extensions/preview": (c) => { posts.push(c); return jsonResponse(preview); },
        "POST /api/admin/extensions": (c) => { posts.push(c); history = [applied]; return jsonResponse(applied, 201); },
      },
    });
    expect(await screen.findByText("None yet.")).toBeInTheDocument();
    await choose("Filing", "GSTR-1 · Outward supplies");
    await choose("Period", "Sep 2026");
    expect(screen.getByText("2 open cycles, due Mon 12 Oct 2026")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("New due date"), "2026-10-20");
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));

    expect(await screen.findByTestId("extension-summary")).toHaveTextContent("2 cycles of GSTR-1 Sep 2026 will move to Tue 20 Oct 2026.");
    const table = screen.getByRole("table", { name: "Cycles that will move" });
    expect(table).toHaveTextContent("Mon 12 Oct 2026 → Tue 20 Oct 2026");
    expect(table).toHaveTextContent("Thu 8 Oct 2026 → Fri 16 Oct 2026");
    const apply = screen.getByRole("button", { name: "Apply to 2 cycles" });
    expect(apply).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Reason"), "Notification 12/2026");
    await userEvent.click(apply);

    expect(await screen.findByText("GSTR-1 Sep 2026 is now due Tue 20 Oct 2026. 2 cycles moved.")).toBeInTheDocument();
    expect(posts[0]?.body).toEqual({ filing_id: 3, period_start: "2026-09-01", new_due_date: "2026-10-20" });
    expect(posts[1]?.body).toEqual({ filing_id: 3, period_start: "2026-09-01", new_due_date: "2026-10-20", reason: "Notification 12/2026", expected_changes: 2 });
    expect(posts[1]?.headers.get("X-CSRF-Token")).toBe("csrf-1");
    await waitFor(() => { expect(screen.getByRole("list", { name: "Extensions applied" })).toHaveTextContent("GSTR-1 Sep 2026: due Tue 20 Oct 2026 (2 cycles)"); });
  });

  it("asks for a fresh preview when the cycles changed meanwhile", async () => {
    renderApp("/setup/extensions", {
      user: admin,
      handlers: {
        ...quiet,
        "GET /api/admin/filings": () => jsonResponse([filing]),
        "GET /api/admin/extensions": () => jsonResponse([]),
        "GET /api/admin/extensions/periods?filing_id=3": () => jsonResponse([period]),
        "POST /api/admin/extensions/preview": () => jsonResponse(preview),
        "POST /api/admin/extensions": () => errorResponse(409, "preview_stale", "1 cycles would change now, not 2. Preview again before applying."),
      },
    });
    await screen.findByText("None yet.");
    await choose("Filing", "GSTR-1 · Outward supplies");
    await choose("Period", "Sep 2026");
    await userEvent.type(screen.getByLabelText("New due date"), "2026-10-20");
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    await userEvent.type(await screen.findByLabelText("Reason"), "Notification 12/2026");
    await userEvent.click(screen.getByRole("button", { name: "Apply to 2 cycles" }));
    expect(await screen.findByText("1 cycles would change now, not 2. Preview again before applying.")).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Cycles that will move" })).not.toBeInTheDocument();
  });
});
