import { screen, waitFor, within } from "@testing-library/react";
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
  task: null,
  history,
  can_edit: true,
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

describe("Task tickets", () => {
  const task = (status: string, over: Record<string, unknown> = {}) => ({
    code: "TKT-000050",
    type: "task",
    title: "Collect signed engagement letter",
    client: { client_id: 100001, legal_name: "Asha Rao" },
    created_at: "2026-10-14T05:00:00Z",
    cycle: null,
    task: { description: "Bring the original", assignee: { id: 3, full_name: "Bina Shah" }, due_date: "2026-11-20", status, status_label: status === "open" ? "Open" : "Done", completed_at: status === "done" ? "2026-10-15T05:00:00Z" : null },
    history: [],
    can_edit: false,
    ...over,
  });

  it("lets the assignee mark it done, and nothing more", async () => {
    const patches: Call[] = [];
    renderApp("/tickets/TKT-000050", {
      user: staff,
      handlers: {
        ...quiet,
        "GET /api/tickets/TKT-000050": () => jsonResponse(task("open")),
        "PATCH /api/tickets/TKT-000050": (c) => { patches.push(c); return jsonResponse(task("done")); },
      },
    });
    expect(await screen.findByTestId("task-status")).toHaveTextContent("Open");
    expect(screen.getByText("Fri 20 Nov 2026")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Edit task" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel task" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "← Work list" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Mark done" }));
    expect(await screen.findByRole("button", { name: "Reopen" })).toBeInTheDocument();
    expect(patches[0]?.body).toEqual({ status: "done", expected_status: "open" });
  });

  it("lets the owner edit it", async () => {
    const patches: Call[] = [];
    renderApp("/tickets/TKT-000050", {
      user: admin,
      handlers: {
        ...quiet,
        "GET /api/tickets/TKT-000050": () => jsonResponse(task("open", { can_edit: true })),
        "GET /api/tickets/assignees": () => jsonResponse([{ id: 3, full_name: "Bina Shah" }, { id: 4, full_name: "Chen Iyer" }]),
        "PATCH /api/tickets/TKT-000050": (c) => { patches.push(c); return jsonResponse(task("open", { can_edit: true })); },
      },
    });
    await userEvent.click(await screen.findByRole("combobox", { name: "Assigned to" }));
    await userEvent.click(await screen.findByRole("option", { name: "Chen Iyer" }));
    await userEvent.click(screen.getByRole("button", { name: "Save task" }));
    await waitFor(() => { expect(patches).toHaveLength(1); });
    expect(patches[0]?.body).toEqual({ title: "Collect signed engagement letter", description: "Bring the original", assignee_id: 4, due_date: "2026-11-20" });
  });
});

describe("Work list", () => {
  const row = (code: string, over: Record<string, unknown> = {}) => ({
    code, type: "filing", title: "GSTR-1 Apr 2026", client: { client_id: 100001, legal_name: "Asha Rao" },
    owner: { id: 2, full_name: "Asha Rao" }, assignee: null, state: "scheduled", state_label: "Scheduled", due_date: "2026-05-11", ...over,
  });

  it("lists open work, soonest first, and filters by type", async () => {
    renderApp("/work", {
      user: staff,
      handlers: {
        ...quiet,
        "GET /api/tickets/assignees": () => jsonResponse([{ id: 3, full_name: "Bina Shah" }]),
        "GET /api/tickets?limit=50&offset=0&open_only=true": () => jsonResponse({ items: [row("TKT-000001"), row("TKT-000050", { type: "task", title: "Call client", state: "open", state_label: "Open", assignee: { id: 3, full_name: "Bina Shah" }, due_date: "2026-11-20" })], total: 2, offset: 0, limit: 50 }),
        "GET /api/tickets?limit=50&offset=0&type=task&open_only=true": () => jsonResponse({ items: [row("TKT-000050", { type: "task", title: "Call client", state: "open", state_label: "Open" })], total: 1, offset: 0, limit: 50 }),
      },
    });
    const list = await screen.findByRole("list", { name: "Tickets" });
    expect(within(list).getByRole("link", { name: /TKT-000001 · GSTR-1 Apr 2026/ })).toHaveAttribute("href", "/tickets/TKT-000001");
    expect(list).toHaveTextContent("Open · due Fri 20 Nov 2026 · for Bina Shah");
    await userEvent.click(screen.getByRole("combobox", { name: "Type" }));
    await userEvent.click(await screen.findByRole("option", { name: "Tasks" }));
    expect(await screen.findByText("1 ticket")).toBeInTheDocument();
  });
});
