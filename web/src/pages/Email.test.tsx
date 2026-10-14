import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { admin, jsonResponse, renderApp, staff, stubScreen, type Call } from "../test-utils";

const quiet = { "GET /api/health/ready": () => new Promise<Response>(() => undefined), "GET /api/admin/audit/status": () => jsonResponse({ latest: null }) };

const row = (over: Record<string, unknown> = {}) => ({
  id: 1,
  kind: "test",
  status: "delivered",
  to: ["boss@firm.in"],
  subject: "Test email from Ledgerline",
  client: null,
  ticket: null,
  attempts: 1,
  last_error: null,
  created_at: "2026-10-14T05:00:00Z",
  queued_at: "2026-10-14T05:00:00Z",
  sent_at: "2026-10-14T05:00:05Z",
  delivered_at: "2026-10-14T05:00:09Z",
  failed_at: null,
  ...over,
});

const detail = (over: Record<string, unknown> = {}) => ({
  ...row(over),
  cc: [],
  body: "This is a test email from Ledgerline, sent by Boss.\n",
  next_attempt_at: null,
  events: [
    { status: "draft", source: "app", detail: "drafted", occurred_at: "2026-10-14T05:00:00Z" },
    { status: "queued", source: "app", detail: "queued", occurred_at: "2026-10-14T05:00:00Z" },
    { status: "sent", source: "worker", detail: "accepted on try 1", occurred_at: "2026-10-14T05:00:05Z" },
    { status: "delivered", source: "provider", detail: null, occurred_at: "2026-10-14T05:00:09Z" },
  ],
  ...over,
});

beforeEach(() => {
  stubScreen(true);
});

describe("Email outbox", () => {
  it("is for the Administrator only", async () => {
    renderApp("/setup/email", { user: staff, handlers: quiet });
    expect(await screen.findByText("You do not have access to this page.")).toBeInTheDocument();
  });

  it("lists sent email with its status and shows how one went", async () => {
    renderApp("/setup/email", {
      user: admin,
      handlers: {
        ...quiet,
        "GET /api/admin/emails?limit=50&offset=0": () => jsonResponse({ items: [row(), row({ id: 2, status: "failed", kind: "audit_alert", subject: "Action needed", attempts: 3, last_error: "the mail server replied 550" })], total: 2, offset: 0, limit: 50 }),
        "GET /api/admin/emails?limit=50&offset=0&status=failed": () => jsonResponse({ items: [row({ id: 2, status: "failed", subject: "Action needed" })], total: 1, offset: 0, limit: 50 }),
        "GET /api/admin/emails/1": () => jsonResponse(detail()),
      },
    });
    const outbox = await screen.findByRole("list", { name: "Outbox" });
    expect(within(outbox).getAllByRole("button")).toHaveLength(2);
    expect(within(outbox).getByText("Failed")).toBeInTheDocument();
    expect(outbox).toHaveTextContent("Audit alarm");

    await userEvent.click(within(outbox).getByText("Test email from Ledgerline"));
    const history = await screen.findByRole("list", { name: "Email history" });
    expect(history).toHaveTextContent("Delivered · Email provider");
    expect(history).toHaveTextContent("Sent · Send worker");
    expect(screen.getByText(/sent by Boss/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("combobox", { name: "Status" }));
    await userEvent.click(await screen.findByRole("option", { name: "Failed" }));
    expect(await screen.findAllByText("Action needed")).toHaveLength(1);
  });

  it("queues a test email, to yourself by default", async () => {
    const posts: Call[] = [];
    renderApp("/setup/email", {
      user: admin,
      handlers: {
        ...quiet,
        "GET /api/admin/emails?limit=50&offset=0": () => jsonResponse({ items: [], total: 0, offset: 0, limit: 50 }),
        "POST /api/admin/emails/test": (c) => { posts.push(c); return jsonResponse(detail({ status: "queued", sent_at: null, delivered_at: null, events: [] }), 201); },
      },
    });
    expect(await screen.findByText("No emails.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Send test email" }));
    const shown = await screen.findByRole("region", { name: "Email details" });
    expect(shown).toHaveTextContent("Queued");
    expect(posts[0]?.body).toEqual({});
    expect(posts[0]?.headers.get("X-CSRF-Token")).toBe("csrf-1");
  });
});
