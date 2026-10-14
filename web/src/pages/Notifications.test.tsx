import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { admin, jsonResponse, renderApp, stubScreen, type Call } from "../test-utils";

const quiet = { "GET /api/health/ready": () => new Promise<Response>(() => undefined), "GET /api/admin/audit/status": () => jsonResponse({ latest: null }) };

const note = (over: Record<string, unknown> = {}) => ({
  id: 9,
  kind: "escalation",
  title: "Escalation: nothing received for Asha Rao, GSTR-1 Apr 2026",
  body: "TKT-000001 is due on 11 May 2026 and no document has arrived. Missing:\n- Sales register",
  client_id: 100001,
  ticket: "TKT-000001",
  created_at: "2026-05-09T04:30:00Z",
  read: false,
  dismissed_at: null,
  dismissed_by: null,
  dismiss_note: null,
  needs_note: true,
  ...over,
});

beforeEach(() => {
  stubScreen(true);
});

describe("Notifications", () => {
  it("shows the unread count on the bell", async () => {
    renderApp("/", {
      user: admin,
      handlers: { ...quiet, "GET /api/notifications/count": () => jsonResponse({ unread: 3, open_escalations: 1 }) },
    });
    expect(await screen.findByRole("link", { name: "Notifications, 3 unread" })).toHaveAttribute("href", "/notifications");
  });

  it("will not dismiss an escalation without a note", async () => {
    const posts: Call[] = [];
    let items = [note(), note({ id: 8, kind: "documents_received", title: "Documents received: Bina Shah, GSTR-3B Apr 2026", body: "Every required document has arrived.", needs_note: false })];
    renderApp("/notifications", {
      user: admin,
      handlers: {
        ...quiet,
        "GET /api/notifications/count": () => jsonResponse({ unread: 2, open_escalations: 1 }),
        "GET /api/notifications?show=open": () => jsonResponse({ items, unread: 2 }),
        "POST /api/notifications/9/dismiss": (c) => { posts.push(c); items = items.slice(1); return jsonResponse(note({ dismissed_at: "2026-05-09T05:00:00Z" })); },
      },
    });
    const escalation = await screen.findByRole("article", { name: /Escalation: nothing received/ });
    expect(escalation).toHaveTextContent("- Sales register");
    await userEvent.click(within(escalation).getByRole("button", { name: "Dismiss" }));
    const confirm = within(escalation).getByRole("button", { name: "Dismiss" });
    expect(confirm).toBeDisabled();
    await userEvent.type(within(escalation).getByLabelText(/What was done about it\?/), "ok");
    expect(confirm).toBeDisabled();
    await userEvent.type(within(escalation).getByLabelText(/What was done about it\?/), " - called, documents promised Friday");
    await userEvent.click(confirm);
    expect(posts[0]?.body).toEqual({ note: "ok - called, documents promised Friday" });
    expect(await screen.findAllByRole("article")).toHaveLength(1);
    expect(screen.getByRole("article", { name: /Documents received/ })).toBeInTheDocument();
  });
});
