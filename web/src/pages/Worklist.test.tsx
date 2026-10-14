import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { admin, jsonResponse, renderApp, staff, stubScreen } from "../test-utils";

const quiet = { "GET /api/health/ready": () => new Promise<Response>(() => undefined), "GET /api/admin/audit/status": () => jsonResponse({ latest: null }) };

const row = (code: string, over: Record<string, unknown> = {}) => ({
  code,
  client: { client_id: 100001, legal_name: "Asha Rao" },
  filing: "GSTR-1",
  period_label: "Apr 2026",
  due_date: "2026-05-11",
  stage: "awaiting_documents",
  stage_label: "Awaiting documents",
  missing: [],
  ...over,
});

const worklist = (owner: unknown = null) => ({
  on: "2026-05-11",
  week_from: "2026-05-11",
  week_to: "2026-05-17",
  owner,
  counts: { received: 1, missing: 2, due_this_week: 3, overdue: 0 },
  lists: {
    received: { total: 1, items: [row("TKT-000003", { stage: "documents_received", stage_label: "Documents received" })] },
    missing: { total: 2, items: [row("TKT-000001", { missing: ["Sales register", "Purchase register"] }), row("TKT-000002")] },
    due_this_week: { total: 3, items: [row("TKT-000001"), row("TKT-000002"), row("TKT-000003")] },
    overdue: { total: 0, items: [] },
  },
});

beforeEach(() => {
  stubScreen(true);
});

describe("Daily work list", () => {
  it("shows the four counts and each list, with what is missing", async () => {
    renderApp("/", { user: staff, handlers: { ...quiet, "GET /api/worklist": () => jsonResponse(worklist({ id: 2, full_name: "Asha Rao" })) } });
    expect(await screen.findByRole("heading", { name: "Today, Mon 11 May 2026" })).toBeInTheDocument();
    expect(screen.getByTestId("count-missing")).toHaveTextContent("2");
    expect(screen.getByTestId("count-due_this_week")).toHaveTextContent("3");
    expect(screen.getByRole("list", { name: "Documents received" })).toHaveTextContent("TKT-000003 · Documents received");
    await userEvent.click(screen.getByRole("tab", { name: /Waiting on client/ }));
    const missing = screen.getByRole("list", { name: "Waiting on client" });
    expect(within(missing).getAllByRole("link")).toHaveLength(2);
    expect(missing).toHaveTextContent("Missing: Sales register, Purchase register");
    await userEvent.click(screen.getByRole("tab", { name: /Overdue/ }));
    expect(screen.getByText("Nothing is overdue.")).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Whose clients" })).not.toBeInTheDocument();
  });

  it("lets the Administrator look at one owner's list", async () => {
    renderApp("/", {
      user: admin,
      handlers: {
        ...quiet,
        "GET /api/tickets/assignees": () => jsonResponse([{ id: 2, full_name: "Asha Rao" }]),
        "GET /api/worklist": () => jsonResponse(worklist()),
        "GET /api/worklist?owner_id=2": () => jsonResponse({ ...worklist({ id: 2, full_name: "Asha Rao" }), counts: { received: 0, missing: 0, due_this_week: 1, overdue: 0 } }),
      },
    });
    await waitFor(() => { expect(screen.getByTestId("count-due_this_week")).toHaveTextContent("3"); });
    await userEvent.click(screen.getByRole("combobox", { name: "Whose clients" }));
    await userEvent.click(await screen.findByRole("option", { name: "Asha Rao" }));
    await waitFor(() => { expect(screen.getByTestId("count-due_this_week")).toHaveTextContent("1"); });
  });
});
