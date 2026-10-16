import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { admin, errorResponse, jsonResponse, renderApp, staff, stubScreen, type Call } from "../test-utils";

const quiet = {
  "GET /api/health/ready": () => new Promise<Response>(() => undefined),
  "GET /api/admin/audit/status": () => jsonResponse({ latest: null }),
  "GET /api/tickets/TKT-000001/links": () => jsonResponse([]),
  "GET /api/tickets/TKT-000001/documents": () => jsonResponse([]),
  "GET /api/tickets/TKT-000001/checklist": () => jsonResponse([]),
  "GET /api/tickets/TKT-000001/emails": () => jsonResponse([]),
  "GET /api/tickets/TKT-000001/assignment": () => jsonResponse({ current: null, history: [] }),
  "GET /api/assignment-candidates": () => jsonResponse([]),
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
    const confirm = screen.getByRole("button", { name: "Return to the preparer" });
    expect(confirm).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/^Comments for the preparer/), "Totals do not match");
    await userEvent.click(confirm);
    expect(await screen.findByRole("button", { name: "Send for review" })).toBeInTheDocument();
    expect(posts[0]?.body).toEqual({ action: "return", expected_stage: "in_review", note: "Totals do not match" });
    expect(posts[0]?.headers.get("X-CSRF-Token")).toBe("csrf-1");
    expect(screen.getByRole("list", { name: "Ticket history" })).toHaveTextContent("In review → Preparing");
    expect(screen.getByRole("list", { name: "Ticket history" })).toHaveTextContent("“Totals do not match”");
  });

  it("does not offer approval to the preparer, and says why", async () => {
    renderApp("/tickets/TKT-000001", {
      user: staff,
      handlers: { ...quiet, "GET /api/tickets/TKT-000001": () => jsonResponse({ ...ticket("in_review", [move("return", "Return to the preparer", "in_review", "preparing", true)]), cycle: { ...ticket("in_review", [move("return", "Return to the preparer", "in_review", "preparing", true)]).cycle, approve_blocked: "You prepared this work, so someone else must approve it." } }) },
    });
    expect(await screen.findByText("You prepared this work, so someone else must approve it.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  it("approves with an optional comment", async () => {
    const posts: Call[] = [];
    renderApp("/tickets/TKT-000001", {
      user: admin,
      handlers: {
        ...quiet,
        "GET /api/tickets/TKT-000001": () => jsonResponse(ticket("in_review", [move("approve", "Approve", "in_review", "ready_to_file")])),
        "POST /api/tickets/TKT-000001/transition": (c) => { posts.push(c); return jsonResponse(ticket("ready_to_file", [move("file", "Mark filed", "ready_to_file", "filed")])); },
      },
    });
    await userEvent.type(await screen.findByLabelText(/^Comments for the preparer/), "Looks right");
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("button", { name: "Mark filed" })).toBeInTheDocument();
    expect(posts[0]?.body).toEqual({ action: "approve", expected_stage: "in_review", note: "Looks right" });
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

describe("Overdue fees on a filing", () => {
  it("shows the block and lets an Administrator override this filing", async () => {
    const posts: Call[] = [];
    const fees = { mode: "block", overdue: "1180.00", warning: "This client has ₹1180.00 in overdue fees. Filing is blocked until they are paid or an Administrator overrides it.", blocked: true, waiver: null };
    const base = ticket("ready_to_file", []);
    renderApp("/tickets/TKT-000001", {
      user: admin,
      handlers: {
        ...quiet,
        "GET /api/tickets/TKT-000001": () => jsonResponse({ ...base, cycle: { ...base.cycle, can_file: true, fees } }),
        "GET /api/tickets/TKT-000001/documents": () => jsonResponse([]),
        "POST /api/tickets/TKT-000001/fee-waiver": (c) => { posts.push(c); return jsonResponse({ ...base, cycle: { ...base.cycle, can_file: true, fees: { ...fees, blocked: false, waiver: { reason: "Deadline today", granted_by: "The Boss", granted_at: "2026-10-14T06:00:00Z" } } } }, 201); },
      },
    });
    expect(await screen.findByText(/Filing is blocked/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Override for this filing" }));
    await userEvent.type(screen.getByLabelText("Why let this filing go ahead?"), "Deadline today");
    await userEvent.click(screen.getByRole("button", { name: "Override for this filing" }));
    expect(await screen.findByText("Overridden for this filing by The Boss: “Deadline today”")).toBeInTheDocument();
    expect(posts[0]?.body).toEqual({ reason: "Deadline today" });
  });

  it("does not offer staff the override", async () => {
    const base = ticket("ready_to_file", []);
    renderApp("/tickets/TKT-000001", {
      user: staff,
      handlers: {
        ...quiet,
        "GET /api/tickets/TKT-000001": () => jsonResponse({ ...base, cycle: { ...base.cycle, can_file: true, fees: { mode: "warn", overdue: "50.00", warning: "This client has ₹50.00 in overdue fees.", blocked: false, waiver: null } } }),
        "GET /api/tickets/TKT-000001/documents": () => jsonResponse([]),
      },
    });
    expect(await screen.findByText("This client has ₹50.00 in overdue fees.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Override for this filing" })).not.toBeInTheDocument();
  });
});

describe("Filing", () => {
  it("marks it filed only with both the acknowledgement and the reference number", async () => {
    const uploads: Call[] = [];
    const ready = { ...ticket("ready_to_file", []), cycle: { ...ticket("ready_to_file", []).cycle, due_date: "2099-01-31", can_file: true, proof: null } };
    const filed = { ...ticket("filed", [move("acknowledge", "Mark acknowledged", "filed", "acknowledged")]), cycle: { ...ticket("filed", [move("acknowledge", "Mark acknowledged", "filed", "acknowledged")]).cycle, can_file: false, proof: { reference: "AA0705260012345", version_id: 77, filename: "ack.pdf", filed_by: "Asha Rao", filed_at: "2026-05-10T05:00:00Z" } } };
    renderApp("/tickets/TKT-000001", {
      user: staff,
      handlers: {
        ...quiet,
        "GET /api/tickets/TKT-000001": () => jsonResponse(ready),
        "GET /api/tickets/TKT-000001/documents": () => jsonResponse([]),
        "POST /api/uploads/tickets/TKT-000001/filing?reference=AA0705260012345": (c) => { uploads.push(c); return jsonResponse(filed); },
      },
    });
    const go = await screen.findByRole("button", { name: "Mark filed" });
    expect(go).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Reference number"), "AA 07");
    expect(screen.getByText("5 to 50 letters and digits, exactly as the portal gave it.")).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText("Reference number"));
    await userEvent.type(screen.getByLabelText("Reference number"), "AA0705260012345");
    expect(go).toBeDisabled(); // still no file
    await userEvent.upload(screen.getByLabelText("Acknowledgement file"), new File(["%PDF-1.7"], "ack.pdf", { type: "application/pdf" }));
    expect(screen.getByTestId("filing-file")).toHaveTextContent("ack.pdf");
    await userEvent.click(go);
    expect(await screen.findByTestId("filing-proof")).toHaveTextContent("Filed · reference AA0705260012345");
    expect(screen.getByRole("button", { name: "Mark acknowledged" })).toBeInTheDocument();
    expect(uploads[0]?.headers.get("X-File-Name")).toBe("ack.pdf");
  });

  it("asks why when filing after the due date", async () => {
    const uploads: Call[] = [];
    const late = { ...ticket("ready_to_file", []), cycle: { ...ticket("ready_to_file", []).cycle, due_date: "2020-01-31", can_file: true, proof: null } };
    renderApp("/tickets/TKT-000001", {
      user: staff,
      handlers: {
        ...quiet,
        "GET /api/tickets/TKT-000001": () => jsonResponse(late),
        "GET /api/tickets/TKT-000001/documents": () => jsonResponse([]),
        "POST /api/uploads/tickets/TKT-000001/filing?reference=AA0705260012345&late_reason=Client%20delayed": (c) => { uploads.push(c); return jsonResponse(late); },
      },
    });
    const go = await screen.findByRole("button", { name: "Mark filed" });
    await userEvent.type(screen.getByLabelText("Reference number"), "AA0705260012345");
    await userEvent.upload(screen.getByLabelText("Acknowledgement file"), new File(["%PDF-1.7"], "ack.pdf", { type: "application/pdf" }));
    expect(go).toBeDisabled(); // no reason yet
    await userEvent.type(screen.getByLabelText("Why is it late?"), "Client delayed");
    await userEvent.click(go);
    await waitFor(() => { expect(uploads).toHaveLength(1); });
  });

  it("tells someone who may not file who can", async () => {
    renderApp("/tickets/TKT-000001", {
      user: staff,
      handlers: { ...quiet, "GET /api/tickets/TKT-000001": () => jsonResponse({ ...ticket("ready_to_file", []), cycle: { ...ticket("ready_to_file", []).cycle, can_file: false } }), "GET /api/tickets/TKT-000001/documents": () => jsonResponse([]) },
    });
    expect(await screen.findByText("Ready for the client's owner or an Administrator to file.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mark filed" })).not.toBeInTheDocument();
  });
});

describe("Documents", () => {
  const version = (n: number, over: Record<string, unknown> = {}) => ({ id: 100 + n, version: n, filename: `Sales v${String(n)}.pdf`, content_type: "application/pdf", size_bytes: 2048, uploaded_via: "staff", uploaded_by: "Asha Rao", uploaded_at: "2026-10-14T05:00:00Z", scan_status: "clean", scan_label: "Checked", ...over });

  it("lists versions newest first and uploads a new version of a document", async () => {
    const uploads: Call[] = [];
    let docs = [{ id: 5, title: "Sales register", latest_version: 1, versions: [version(1)] }];
    renderApp("/tickets/TKT-000001", {
      user: staff,
      handlers: {
        ...quiet,
        "GET /api/tickets/TKT-000001": () => jsonResponse(ticket("scheduled", [])),
        "GET /api/tickets/TKT-000001/documents": () => jsonResponse(docs),
        "POST /api/uploads/tickets/TKT-000001?document_id=5": (c) => {
          uploads.push(c);
          docs = [{ id: 5, title: "Sales register", latest_version: 2, versions: [version(2, { scan_status: "pending", scan_label: "Waiting for the virus check" }), version(1)] }];
          return jsonResponse(docs, 201);
        },
      },
    });
    const list = await screen.findByRole("list", { name: "Versions of Sales register" });
    expect(list).toHaveTextContent("Version 1 · Sales v1.pdf");
    expect(within(list).getByRole("button", { name: "Download" })).toBeInTheDocument();

    const file = new File(["%PDF-1.7"], "Sales (corrected).pdf", { type: "application/pdf" });
    await userEvent.upload(screen.getByLabelText("Upload a new version"), file);
    expect(await screen.findByText(/will be available once the virus check has run/)).toBeInTheDocument();
    expect(uploads[0]?.headers.get("X-File-Name")).toBe("Sales%20(corrected).pdf");
    expect(uploads[0]?.headers.get("X-CSRF-Token")).toBe("csrf-1");
    const updated = screen.getByRole("list", { name: "Versions of Sales register" });
    expect(within(updated).getAllByRole("listitem")[0]).toHaveTextContent("Version 2");
    expect(updated).toHaveTextContent("Waiting for the virus check");
    expect(within(updated).getAllByRole("button", { name: "Download" })).toHaveLength(1);
  });
});

describe("Emails to the client", () => {
  it("flags a failed alert and takes it off the list once followed up", async () => {
    const posts: Call[] = [];
    const failed = { id: 77, kind_label: "Alert", status: "failed", subject: "GSTR-1 Apr 2026: documents needed", to: ["asha@example.in"], queued_at: "2026-10-14T05:00:00Z", attempts: 1, last_error: "the mail server replied 550", problem: "failed", problem_label: "Email to the client failed: call them", followed_up_by: null, followed_up_at: null };
    let emails: Record<string, unknown>[] = [failed];
    renderApp("/tickets/TKT-000001", {
      user: staff,
      handlers: {
        ...quiet,
        "GET /api/tickets/TKT-000001": () => jsonResponse(ticket("awaiting_documents", [])),
        "GET /api/tickets/TKT-000001/updates": () => jsonResponse([]),
        "GET /api/tickets/TKT-000001/emails": () => jsonResponse(emails),
        "POST /api/emails/77/followed-up": (c) => {
          posts.push(c);
          emails = [{ ...failed, problem: null, problem_label: null, followed_up_by: "Asha Rao", followed_up_at: "2026-10-14T06:00:00Z" }];
          return jsonResponse(emails[0]);
        },
      },
    });
    const list = await screen.findByRole("list", { name: "Emails to the client" });
    expect(list).toHaveTextContent("Email to the client failed: call them");
    expect(list).toHaveTextContent("the mail server replied 550");
    await userEvent.click(within(list).getByRole("button", { name: "Mark followed up" }));
    expect(await within(list).findByText(/followed up by Asha Rao/)).toBeInTheDocument();
    expect(within(list).queryByRole("button", { name: "Mark followed up" })).not.toBeInTheDocument();
    expect(posts).toHaveLength(1);
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

describe("Nil filings and phone calls (C34)", () => {
  it("shows the reviewer the client's own no-activity confirmation", async () => {
    const t = ticket("in_review", [move("approve", "Approve", "in_review", "ready_to_file")]);
    renderApp("/tickets/TKT-000001", {
      user: staff,
      handlers: { ...quiet, "GET /api/tickets/TKT-000001": () => jsonResponse({ ...t, cycle: { ...t.cycle, nil_confirmation: { confirmed_at: "2026-05-02T05:00:00Z", note: "Shop closed" } } }) },
    });
    expect(await screen.findByTestId("nil-confirmation")).toHaveTextContent("The client confirmed no activity in this period");
    expect(screen.getByTestId("nil-confirmation")).toHaveTextContent("“Shop closed”");
  });

  it("notes a call with the promised date and lists earlier calls", async () => {
    const posts: Call[] = [];
    const earlier = { id: 1, by: "Asha Rao", note: "Left a message", promised_on: "2026-05-05", created_at: "2026-05-01T05:00:00Z", reminder: "reminded" };
    renderApp("/tickets/TKT-000001", {
      user: staff,
      handlers: {
        ...quiet,
        "GET /api/tickets/TKT-000001": () => jsonResponse({ ...ticket("preparing", []), phone_contacts: [earlier] }),
        "POST /api/tickets/TKT-000001/phone-contacts": (c) => {
          posts.push(c);
          return jsonResponse([{ id: 2, by: "Asha Rao", note: "Will send registers", promised_on: "2026-05-09", created_at: "2026-05-02T05:00:00Z", reminder: null }, earlier], 201);
        },
      },
    });
    const calls = await screen.findByRole("region", { name: "Phone calls" });
    expect(within(calls).getByText("Left a message")).toBeInTheDocument();
    expect(within(calls).getByText(/reminder sent/)).toBeInTheDocument();
    const save = within(calls).getByRole("button", { name: "Save call" });
    expect(save).toBeDisabled();
    await userEvent.type(within(calls).getByLabelText("What was said"), "Will send registers");
    await userEvent.type(within(calls).getByLabelText("Client promised by (optional)"), "2099-05-09");
    await userEvent.click(save);
    expect(await within(calls).findByText("Will send registers")).toBeInTheDocument();
    expect(posts[0]?.body).toEqual({ note: "Will send registers", promised_on: "2099-05-09" });
  });
});

describe("Client messages on the ticket (C35)", () => {
  it("puts the client's messages in the history, as text", async () => {
    const history = [{ from_stage: "documents_received", to_stage: "assigned", action: "assign", note: null, changed_by: "Asha Rao", changed_at: "2026-05-01T05:00:00Z" }];
    renderApp("/tickets/TKT-000001", {
      user: staff,
      handlers: {
        ...quiet,
        "GET /api/tickets/TKT-000001": () => jsonResponse({ ...ticket("preparing", [], history), messages: [{ id: 3, body: "<b>Sent</b> by courier", sent_at: "2026-05-02T05:00:00Z" }] }),
      },
    });
    const list = await screen.findByRole("list", { name: "Ticket history" });
    const entries = within(list).getAllByText(/./, { selector: ".MuiListItemText-primary" });
    expect(entries[0]).toHaveTextContent("Message from the client: “<b>Sent</b> by courier”");
    expect(entries[1]).toHaveTextContent("Documents received → Assigned");
  });
});

describe("WhatsApp and SMS on the ticket (C36)", () => {
  it("shows each copy's outcome, failures plainly", async () => {
    renderApp("/tickets/TKT-000001", {
      user: staff,
      handlers: {
        ...quiet,
        "GET /api/tickets/TKT-000001": () =>
          jsonResponse({
            ...ticket("preparing", []),
            channel_messages: [
              { id: 2, channel: "sms", status: "failed", text: "x", created_at: "2026-05-09T05:00:00Z", finished_at: "2026-05-09T05:01:00Z", error: "number not on SMS" },
              { id: 1, channel: "whatsapp", status: "sent", text: "x", created_at: "2026-05-07T05:00:00Z", finished_at: "2026-05-07T05:01:00Z", error: null },
            ],
          }),
      },
    });
    const list = await screen.findByRole("list", { name: "WhatsApp and SMS messages" });
    expect(within(list).getByText("SMS: failed: the email went instead")).toBeInTheDocument();
    expect(within(list).getByText(/number not on SMS/)).toBeInTheDocument();
    expect(within(list).getByText("WhatsApp: sent")).toBeInTheDocument();
  });
});
