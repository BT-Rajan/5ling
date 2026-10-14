import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { admin, errorResponse, jsonResponse, renderApp, staff, stubScreen, type Call } from "../test-utils";

const quiet = {
  "GET /api/health/ready": () => new Promise<Response>(() => undefined),
  "GET /api/admin/audit/status": () => jsonResponse({ latest: null }),
  "GET /api/tickets/TKT-000001/links": () => jsonResponse([]),
};

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
    extensions: [],
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

describe("Client link", () => {
  const link = (over: Record<string, unknown> = {}) => ({ id: 7, status: "waiting", status_label: "Not opened yet", origin: "staff", created_by: "Asha Rao", created_at: "2026-10-14T05:00:00Z", expires_at: "2026-10-17T05:00:00Z", opened_at: null, last_seen_at: null, ...over });

  it("emails a new link, or makes one to copy, and withdraws one", async () => {
    const posts: Call[] = [];
    let links = [link({ id: 6, status: "opened", status_label: "Opened", opened_at: "2026-10-14T06:00:00Z" })];
    renderApp("/tickets/TKT-000001", {
      user: staff,
      handlers: {
        ...quiet,
        "GET /api/tickets/TKT-000001": () => jsonResponse(ticket("scheduled", [])),
        "GET /api/tickets/TKT-000001/links": () => jsonResponse(links),
        "POST /api/tickets/TKT-000001/links": (c) => {
          posts.push(c);
          const sent = (c.body as { send_email: boolean }).send_email;
          links = [link(), ...links];
          return jsonResponse({ link: link(), url: "http://localhost:5173/portal#t=secret", emailed_to: sent ? "a•••@example.in" : null }, 201);
        },
        "POST /api/tickets/TKT-000001/links/6/withdraw": (c) => { posts.push(c); return jsonResponse(link({ id: 6, status: "ended", status_label: "Ended" })); },
      },
    });
    const list = await screen.findByRole("list", { name: "Client links" });
    expect(list).toHaveTextContent("Opened");
    await userEvent.click(screen.getByRole("button", { name: "Email a new link to the client" }));
    expect(await screen.findByText(/on its way to a•••@example.in/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Private link")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Make a link to copy" }));
    expect(await screen.findByLabelText("Private link")).toHaveValue("http://localhost:5173/portal#t=secret");
    expect(posts.map((p) => p.body)).toEqual([{ send_email: true }, { send_email: false }]);
    const withdraw = await screen.findAllByRole("button", { name: "Withdraw" });
    await userEvent.click(withdraw[withdraw.length - 1] as HTMLElement);
    expect(await screen.findByText("Withdrawn. It stops working at once.")).toBeInTheDocument();
  });
});

describe("Updates to the client", () => {
  const update = (over: Record<string, unknown> = {}) => ({ id: 31, status: "draft", from_state: "scheduled", to_state: "awaiting_documents", subject: "GSTR-1 Apr 2026: Awaiting documents (TKT-000001)", to: ["asha@example.in"], cc: [], created_at: "2026-10-14T05:00:00Z", queued_at: null, sent_by: null, sent_at: null, delivered_at: null, failed_at: null, ...over });
  const preview = (cc: string[], note: string) => ({
    subject: "GSTR-1 Apr 2026: Awaiting documents (TKT-000001)",
    body: `Dear Asha Rao,\n\nYour GSTR-1 for Apr 2026 has moved from Scheduled to Awaiting documents.\n\n${note}\n\nAsha Rao\nLedgerline\n`,
    to: ["asha@example.in"],
    cc,
    other_contacts: [{ id: 9, name: "Ravi Rao", email: "ravi@example.in" }],
  });

  it("shows waiting drafts, previews one, and sends it only on the click", async () => {
    const sends: Call[] = [];
    let list = [update(), update({ id: 30, status: "delivered", from_state: "assigned", to_state: "preparing", sent_by: "Asha Rao", queued_at: "2026-10-13T05:00:00Z" })];
    renderApp("/tickets/TKT-000001", {
      user: staff,
      handlers: {
        ...quiet,
        "GET /api/tickets/TKT-000001": () => jsonResponse(ticket("scheduled", [])),
        "GET /api/tickets/TKT-000001/updates": () => jsonResponse(list),
        "POST /api/updates/31/preview": (c) => {
          const b = c.body as { note: string; cc_contact_ids: number[] };
          return jsonResponse(preview(b.cc_contact_ids.length ? ["ravi@example.in"] : [], b.note));
        },
        "POST /api/updates/31/send": (c) => { sends.push(c); list = [update({ status: "queued", sent_by: "Asha Rao", queued_at: "2026-10-14T06:00:00Z" }), ...list.slice(1)]; return jsonResponse(list[0]); },
      },
    });
    expect(await screen.findByTestId("drafts-badge")).toHaveTextContent("1 to send");
    const updates = screen.getByRole("list", { name: "Updates to the client" });
    expect(updates).toHaveTextContent("Scheduled → Awaiting documents");
    expect(updates).toHaveTextContent("Delivered");
    expect(sends).toHaveLength(0);

    await userEvent.click(screen.getByRole("button", { name: "Preview and send" }));
    expect(await screen.findByTestId("update-preview")).toHaveTextContent("moved from Scheduled to Awaiting documents");
    await userEvent.type(screen.getByLabelText("Personal note (optional)"), "Please send the sales register.");
    await userEvent.click(screen.getByLabelText("Ravi Rao (ravi@example.in)"));
    expect(await screen.findByText(/copy to ravi@example.in/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Send update to client" }));
    expect(await screen.findByText("Sent to the client.")).toBeInTheDocument();
    expect(sends).toHaveLength(1);
    expect(sends[0]?.body).toEqual({ note: "Please send the sales register.", cc_contact_ids: [9] });
    expect(screen.queryByTestId("drafts-badge")).not.toBeInTheDocument();
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
