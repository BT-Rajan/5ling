import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { admin, errorResponse, jsonResponse, renderApp, stubScreen, type Call } from "../test-utils";

const quiet = { "GET /api/health/ready": () => new Promise<Response>(() => undefined), "GET /api/admin/audit/status": () => jsonResponse({ latest: null }) };

const client = { client_id: 100001, kind: "individual", legal_name: "Ravi Kumar", pan: "ABCPE1234F", gstin: null, status: "active", owner: { id: 2, full_name: "Asha Rao" }, package: { id: 5, name: "GST Monthly", version: 1 }, contacts: [], created_at: "2026-10-14T06:00:00Z" };
const rules = {
  waiting_grace_days: 3, reminder_interval_days: 2, escalation_days_before: 2, portal_link_hours: 72, idle_timeout_minutes: 20, break_glass_minutes: 240,
  limits: { waiting_grace_days: { default: 3, min: 0, max: 30 }, reminder_interval_days: { default: 2, min: 1, max: 14 }, escalation_days_before: { default: 2, min: 0, max: 30 }, portal_link_hours: { default: 72, min: 1, max: 720 }, idle_timeout_minutes: { default: 20, min: 5, max: 240 }, break_glass_minutes: { default: 240, min: 15, max: 1440 } },
  updated_by: null, updated_at: "2026-10-01T00:00:00Z",
};

beforeEach(() => {
  stubScreen(true);
});

describe("Break-glass", () => {
  it("asks an Administrator why before opening someone else's client", async () => {
    const posts: Call[] = [];
    let open = false;
    renderApp("/clients/100001", {
      user: admin,
      handlers: {
        ...quiet,
        "GET /api/clients/100001": () => (open ? jsonResponse(client) : new Response(JSON.stringify({ error: { code: "reason_required", message: "This client is not yours.", client_id: 100001, request_id: "r" } }), { status: 403, headers: { "Content-Type": "application/json" } })),
        "GET /api/clients/100001/history": () => jsonResponse([]),
        "GET /api/clients/100001/tickets": () => jsonResponse([]),
        "GET /api/clients/100001/fees": () => errorResponse(404, "not_found", "Not found."),
        "GET /api/clients/options": () => jsonResponse({ owners: [], packages: [] }),
        "POST /api/clients/100001/break-glass": (c) => { posts.push(c); open = true; return jsonResponse({ id: 1, client_id: 100001, client_name: "Ravi Kumar", administrator: "The Boss", reason: "Asha is on leave", granted_at: "2026-10-14T06:00:00Z", expires_at: "2026-10-14T10:00:00Z", active: true }, 201); },
      },
    });
    expect(await screen.findByRole("heading", { name: "This client is not yours" })).toBeInTheDocument();
    const go = screen.getByRole("button", { name: "Open the client" });
    expect(go).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Reason"), "Asha is on leave");
    await userEvent.click(go);
    expect(await screen.findByTestId("client-id")).toHaveTextContent("100001");
    expect(posts[0]?.body).toEqual({ reason: "Asha is on leave" });
  });
});

describe("Rules and access", () => {
  it("changes a rule within its limits and lists break-glass openings", async () => {
    const puts: Call[] = [];
    renderApp("/setup/rules", {
      user: admin,
      handlers: {
        ...quiet,
        "GET /api/admin/rules": () => jsonResponse(rules),
        "GET /api/admin/break-glass": () => jsonResponse([{ id: 1, client_id: 100001, client_name: "Ravi Kumar", administrator: "The Boss", reason: "Asha is on leave", granted_at: "2026-10-14T06:00:00Z", expires_at: "2026-10-14T10:00:00Z", active: false }]),
        "PUT /api/admin/rules": (c) => { puts.push(c); return jsonResponse({ ...rules, idle_timeout_minutes: 45, updated_by: "The Boss" }); },
      },
    });
    const idle = await screen.findByLabelText(/Sign Staff and Administrators out/);
    await userEvent.clear(idle);
    await userEvent.type(idle, "300");
    expect(screen.getByRole("button", { name: "Save rules" })).toBeDisabled(); // over 240
    await userEvent.clear(idle);
    await userEvent.type(idle, "45");
    await userEvent.click(screen.getByRole("button", { name: "Save rules" }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(puts[0]?.body).toEqual({ waiting_grace_days: 3, reminder_interval_days: 2, escalation_days_before: 2, portal_link_hours: 72, idle_timeout_minutes: 45, break_glass_minutes: 240 });
    expect(screen.getByRole("list", { name: "Break-glass openings" })).toHaveTextContent("Ravi Kumar (100001) · The Boss");
  });
});
