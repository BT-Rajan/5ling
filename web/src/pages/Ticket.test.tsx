import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { admin, errorResponse, jsonResponse, renderApp, staff, stubScreen, type Call } from "../test-utils";

const quiet = { "GET /api/health/ready": () => new Promise<Response>(() => undefined), "GET /api/admin/audit/status": () => jsonResponse({ latest: null }) };

const move = (action: string, label: string, source: string, target: string, needs_note = false) => ({ action, label, source, target, needs_note });

const ticket = (stage: string, moves: unknown[], history: unknown[] = []) => ({
  code: "TKT-000001",
  type: "filing",
  title: "GSTR-1 Apr 2026",
  client: { client_id: 100001, legal_name: "Asha Rao" },
  created_at: "2026-04-01T00:00:00Z",
  cycle: {
    filing: { code: "GSTR-1", name: "Outward supplies" },
    period_label: "Apr 2026",
    period_start: "2026-04-01",
    period_end: "2026-04-30",
    due_date: "2026-05-11",
    alert_date: "2026-05-07",
    stage,
    stage_label: stage === "in_review" ? "In review" : "Preparing",
    stage_changed_at: "2026-05-01T00:00:00Z",
    package_name: "GST Monthly",
    package_version: 1,
    calendar_version: 2,
    moves,
  },
  history,
});

beforeEach(() => {
  stubScreen(true);
});

describe("Ticket page", () => {
  it("shows where the cycle is among the ten stages", async () => {
    renderApp("/tickets/TKT-000001", {
      user: staff,
      handlers: { ...quiet, "GET /api/tickets/TKT-000001": () => jsonResponse(ticket("in_review", [move("approve", "Approve", "in_review", "ready_to_file"), move("return", "Return to the preparer", "in_review", "preparing", true)])) },
    });
    expect(await screen.findByTestId("ticket-code")).toHaveTextContent("TKT-000001");
    const stages = screen.getByRole("list", { name: "Stages" });
    const items = within(stages).getAllByRole("listitem");
    expect(items).toHaveLength(10);
    expect(items[5]).toHaveAttribute("aria-current", "step");
    expect(items[5]).toHaveTextContent("In review (now)");
    expect(items[4]).toHaveTextContent("Preparing (done)");
    expect(items[6]).not.toHaveTextContent("(done)");
    expect(screen.getByText("Mon 11 May 2026")).toBeInTheDocument();
    expect(screen.getByText("GST Monthly version 1, calendar version 2")).toBeInTheDocument();
  });

  it("offers only the allowed moves, and asks why before returning work", async () => {
    const posts: Call[] = [];
    renderApp("/tickets/TKT-000001", {
      user: staff,
      handlers: {
        ...quiet,
        "GET /api/tickets/TKT-000001": () => jsonResponse(ticket("in_review", [move("approve", "Approve", "in_review", "ready_to_file"), move("return", "Return to the preparer", "in_review", "preparing", true)])),
        "POST /api/tickets/TKT-000001/transition": (c) => {
          posts.push(c);
          return jsonResponse(ticket("preparing", [move("submit_for_review", "Send for review", "preparing", "in_review")], [{ from_stage: "in_review", to_stage: "preparing", action: "return", note: "Totals do not match", changed_by: "Asha Rao", changed_at: "2026-05-02T05:00:00Z" }]));
        },
      },
    });
    expect(await screen.findByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mark filed" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Return to the preparer" }));
    const confirm = screen.getByRole("button", { name: "Return to the preparer" });
    expect(confirm).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/^Why\?/), "Totals do not match");
    await userEvent.click(confirm);
    expect(await screen.findByRole("button", { name: "Send for review" })).toBeInTheDocument();
    expect(posts[0]?.body).toEqual({ action: "return", expected_stage: "in_review", note: "Totals do not match" });
    expect(posts[0]?.headers.get("X-CSRF-Token")).toBe("csrf-1");
    expect(screen.getByRole("list", { name: "Ticket history" })).toHaveTextContent("In review → Preparing");
    expect(screen.getByRole("list", { name: "Ticket history" })).toHaveTextContent("“Totals do not match”");
  });

  it("says when someone else has already moved it", async () => {
    renderApp("/tickets/TKT-000001", {
      user: admin,
      handlers: {
        ...quiet,
        "GET /api/tickets/TKT-000001": () => jsonResponse(ticket("in_review", [move("approve", "Approve", "in_review", "ready_to_file")])),
        "POST /api/tickets/TKT-000001/transition": () => errorResponse(409, "stage_changed", "This has already moved to Ready to file. Refresh to see it."),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByText("This has already moved to Ready to file. Refresh to see it.")).toBeInTheDocument();
  });

  it("is listed on the client's page", async () => {
    renderApp("/clients/100001", {
      user: staff,
      handlers: {
        ...quiet,
        "GET /api/clients/100001": () => jsonResponse({ client_id: 100001, kind: "individual", legal_name: "Asha Rao", pan: null, gstin: null, status: "active", owner: { id: 2, full_name: "Asha Rao" }, package: { id: 5, name: "GST Monthly", version: 1 }, contacts: [], created_at: "2026-04-01T00:00:00Z" }),
        "GET /api/clients/100001/history": () => jsonResponse([]),
        "GET /api/clients/options": () => jsonResponse({ owners: [], packages: [] }),
        "GET /api/clients/100001/tickets": () => jsonResponse([{ code: "TKT-000001", type: "filing", title: "GSTR-1 Apr 2026", stage: "scheduled", stage_label: "Scheduled", due_date: "2026-05-11" }]),
      },
    });
    const link = await screen.findByRole("link", { name: /TKT-000001 · GSTR-1 Apr 2026/ });
    expect(link).toHaveAttribute("href", "/tickets/TKT-000001");
    expect(link).toHaveTextContent("Scheduled · due Mon 11 May 2026");
  });
});
