import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { admin, jsonResponse, renderApp, staff, stubScreen, type Call } from "../test-utils";

const quiet = { "GET /api/health/ready": () => new Promise<Response>(() => undefined), "GET /api/admin/audit/status": () => jsonResponse({ latest: null }) };

const client = {
  client_id: 100001,
  kind: "individual",
  legal_name: "Ravi Kumar",
  pan: "ABCPE1234F",
  gstin: null,
  status: "active",
  owner: { id: 2, full_name: "Asha Rao" },
  package: { id: 5, name: "GST Monthly", version: 1 },
  contacts: [],
  created_at: "2026-10-14T06:00:00Z",
};

const override = (over: Record<string, unknown> = {}) => ({ id: 9, client_id: 100001, client_name: "Ravi Kumar", original: "1500.00", amount: "1200.00", reason: "Agreed rate", status: "pending", requested_by: "Asha Rao", requested_at: "2026-10-14T06:00:00Z", decided_by: null, decided_at: null, decision_note: null, ...over });
const invoice = { id: 1, invoice_no: "INV/26-27/00001", period_label: "Oct 2026", issue_date: "2026-10-01", due_date: "2026-10-16", description: "GST Monthly: monthly fee, Oct 2026", amount: "1500.00", tax_rate: "18.00", cgst: "135.00", sgst: "135.00", igst: "0.00", tax: "270.00", total: "1770.00" };
const fees = (over: Record<string, unknown> = {}) => ({
  plan: { package_name: "GST Monthly", package_version: 1, frequency: "monthly", package_fee: "1500.00", fee: "1500.00", override: null },
  pending: null,
  overrides: [],
  invoices: [invoice],
  can_set_fee: false,
  ...over,
});

beforeEach(() => {
  stubScreen(true);
});

describe("Client fees", () => {
  it("shows the plan and invoices, and lets staff ask for a different fee", async () => {
    const posts: Call[] = [];
    renderApp("/clients/100001", {
      user: staff,
      handlers: {
        ...quiet,
        "GET /api/clients/100001": () => jsonResponse(client),
        "GET /api/clients/100001/fees": () => jsonResponse(fees()),
        "POST /api/clients/100001/fee-overrides": (c) => { posts.push(c); return jsonResponse(fees({ pending: override() }), 201); },
      },
    });
    expect(await screen.findByTestId("fee-plan")).toHaveTextContent("₹1,500 a month");
    const table = screen.getByRole("table", { name: "Invoices" });
    expect(within(table).getByText("INV/26-27/00001")).toBeInTheDocument();
    expect(table).toHaveTextContent("₹270 (18.00%)");
    expect(table).toHaveTextContent("₹1,770");

    await userEvent.click(screen.getByRole("button", { name: "Ask for a different fee" }));
    const ask = screen.getByRole("button", { name: "Ask for approval" });
    await userEvent.type(screen.getByLabelText(/^Client fee/), "1200");
    expect(ask).toBeDisabled(); // no reason yet
    await userEvent.type(screen.getByLabelText(/^Why this fee/), "Agreed rate");
    await userEvent.click(ask);
    expect(await screen.findByText(/₹1,200 asked for by Asha Rao, waiting for an Administrator/)).toBeInTheDocument();
    expect(posts[0]?.body).toEqual({ amount: "1200", reason: "Agreed rate" });
    expect(screen.queryByRole("button", { name: "Ask for a different fee" })).not.toBeInTheDocument();
  });

  it("shows the original and approved amounts and who approved", async () => {
    renderApp("/clients/100001", {
      user: admin,
      handlers: {
        ...quiet,
        "GET /api/clients/100001": () => jsonResponse(client),
        "GET /api/clients/100001/fees": () => jsonResponse(fees({ plan: { package_name: "GST Monthly", package_version: 1, frequency: "monthly", package_fee: "1500.00", fee: "1200.00", override: override({ status: "approved", decided_by: "The Boss", decided_at: "2026-10-14T07:00:00Z" }) }, invoices: [], can_set_fee: true })),
      },
    });
    const plan = await screen.findByTestId("fee-plan");
    expect(plan).toHaveTextContent("₹1,200 a month");
    expect(plan).toHaveTextContent("package fee ₹1,500, this client's fee approved by The Boss");
    expect(screen.getByRole("button", { name: "Set a fee for this client" })).toBeInTheDocument();
    expect(screen.getByText(/No invoices yet/)).toBeInTheDocument();
  });
});

describe("Fees and tax setup", () => {
  it("approves a client fee and rejects another with a reason", async () => {
    const posts: Call[] = [];
    let waiting = [override(), override({ id: 10, client_id: 100002, client_name: "Meena", amount: "900.00" })];
    renderApp("/setup/fees", {
      user: admin,
      handlers: {
        ...quiet,
        "GET /api/admin/fee-overrides": () => jsonResponse(waiting),
        "GET /api/admin/tax-rates": () => jsonResponse([{ id: 1, code: "GST", rate: "18.00", effective_from: "2017-07-01", note: null, added_by: null, added_at: "2026-10-01T00:00:00Z", in_force: true }]),
        "POST /api/admin/fee-overrides/9/decision": (c) => { posts.push(c); waiting = waiting.filter((o) => o.id !== 9); return jsonResponse(override({ status: "approved" })); },
        "POST /api/admin/fee-overrides/10/decision": (c) => { posts.push(c); waiting = []; return jsonResponse(override({ id: 10, status: "rejected" })); },
      },
    });
    expect(await screen.findAllByTestId("fee-request")).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "Approve ₹1,200" }));
    expect(await screen.findAllByTestId("fee-request")).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    const [confirm] = screen.getAllByRole("button", { name: "Reject" });
    if (!confirm) throw new Error("no Reject button");
    expect(confirm).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Why is it rejected?"), "Already discounted");
    await userEvent.click(confirm);
    expect(await screen.findByText("Nothing waiting.")).toBeInTheDocument();
    expect(posts.map((c) => c.body)).toEqual([{ approve: true }, { approve: false, note: "Already discounted" }]);
    expect(screen.getByRole("list", { name: "GST rates" })).toHaveTextContent("18.00% from");
  });

  it("adds a GST rate from a later date", async () => {
    const posts: Call[] = [];
    const current = { id: 1, code: "GST", rate: "18.00", effective_from: "2017-07-01", note: null, added_by: null, added_at: "2026-10-01T00:00:00Z", in_force: true };
    renderApp("/setup/fees", {
      user: admin,
      handlers: {
        ...quiet,
        "GET /api/admin/fee-overrides": () => jsonResponse([]),
        "GET /api/admin/tax-rates": () => jsonResponse([current]),
        "POST /api/admin/tax-rates": (c) => { posts.push(c); return jsonResponse([{ ...current, id: 2, rate: "12.50", effective_from: "2027-04-01", note: "Budget", added_by: "The Boss", in_force: false }, current], 201); },
      },
    });
    const add = await screen.findByRole("button", { name: "Add rate" });
    await userEvent.type(screen.getByLabelText("New rate (%)"), "12.5");
    expect(add).toBeDisabled(); // no date
    await userEvent.type(screen.getByLabelText("From"), "2027-04-01");
    await userEvent.type(screen.getByLabelText("Note (optional)"), "Budget");
    await userEvent.click(add);
    expect(await screen.findByText(/12.50% from/)).toBeInTheDocument();
    expect(posts[0]?.body).toEqual({ rate: "12.5", effective_from: "2027-04-01", note: "Budget" });
  });
});
