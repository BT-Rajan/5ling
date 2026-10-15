import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { admin, jsonResponse, renderApp, staff, stubScreen, type Call } from "../test-utils";

const quiet = { "GET /api/health/ready": () => new Promise<Response>(() => undefined), "GET /api/admin/audit/status": () => jsonResponse({ latest: null }) };

const client = {
  client_id: 100001,
  kind: "individual",
  legal_name: "Ravi Kumar",
  pan_masked: "AB•••••34F",
  gstin: null,
  status: "active",
  owner: { id: 2, full_name: "Asha Rao" },
  package: { id: 5, name: "GST Monthly", version: 1 },
  contacts: [],
  created_at: "2026-10-14T06:00:00Z",
};

const override = (over: Record<string, unknown> = {}) => ({ id: 9, client_id: 100001, client_name: "Ravi Kumar", original: "1500.00", amount: "1200.00", reason: "Agreed rate", status: "pending", requested_by: "Asha Rao", requested_at: "2026-10-14T06:00:00Z", decided_by: null, decided_at: null, decision_note: null, ...over });
const invoice = { id: 1, invoice_no: "INV/26-27/00001", period_label: "Oct 2026", issue_date: "2026-10-01", due_date: "2026-10-16", description: "GST Monthly: monthly fee, Oct 2026", amount: "1500.00", tax_rate: "18.00", cgst: "135.00", sgst: "135.00", igst: "0.00", tax: "270.00", total: "1770.00", paid: "0.00", balance: "1770.00", status: "due", status_label: "Due" };
const fees = (over: Record<string, unknown> = {}) => ({
  plan: { package_name: "GST Monthly", package_version: 1, frequency: "monthly", package_fee: "1500.00", fee: "1500.00", override: null },
  pending: null,
  overrides: [],
  invoices: [invoice],
  ledger: [],
  adjustments: [],
  status: "due",
  status_label: "Due",
  balance: "1770.00",
  overdue: "0.00",
  overdue_limit: null,
  firm_limit: "0.00",
  over_limit: false,
  can_set_fee: false,
  ...over,
});

const policy = { mode: "warn", limit: "0.00", updated_by: null, updated_at: "2026-10-01T00:00:00Z" };
const adjustment = { id: 7, client_id: 100001, client_name: "Ravi Kumar", invoice_id: 1, invoice_no: "INV/26-27/00001", kind: "write_off", kind_label: "Write-off", amount: "200.00", reason: "Hardship", status: "pending", requested_by: "Asha Rao", requested_at: "2026-10-14T06:00:00Z", decided_by: null, decided_at: null, decision_note: null, credit_note_no: null };

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

describe("Payments", () => {
  it("records a part payment and shows the status at once", async () => {
    const posts: Call[] = [];
    const entry = { id: 3, invoice_id: 1, invoice_no: "INV/26-27/00001", kind: "payment", amount: "770.00", entry_date: "2026-10-14", mode: "upi", reference: "UTR123", note: null, reverses_id: null, reversed: false, by: "Asha Rao", at: "2026-10-14T06:00:00Z" };
    renderApp("/clients/100001", {
      user: staff,
      handlers: {
        ...quiet,
        "GET /api/clients/100001": () => jsonResponse(client),
        "GET /api/clients/100001/fees": () => jsonResponse(fees()),
        "POST /api/invoices/1/payments": (c) => {
          posts.push(c);
          return jsonResponse(fees({ status: "part_paid", status_label: "Part paid", balance: "1000.00", invoices: [{ ...invoice, paid: "770.00", balance: "1000.00", status: "part_paid", status_label: "Part paid" }], ledger: [entry] }), 201);
        },
      },
    });
    expect(await screen.findByText("Fees: Due")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Record a payment for INV/26-27/00001" }));
    const form = screen.getByRole("form", { name: "Payment for INV/26-27/00001" });
    const amount = within(form).getByLabelText("Amount (₹)");
    expect(amount).toHaveValue("1770.00");
    await userEvent.clear(amount);
    await userEvent.type(amount, "770");
    await userEvent.click(within(form).getByRole("combobox", { name: "Mode" }));
    await userEvent.click(await screen.findByRole("option", { name: "UPI" }));
    await userEvent.type(within(form).getByLabelText("Reference (optional)"), "UTR123");
    await userEvent.click(within(form).getByRole("button", { name: "Record payment" }));
    expect(await screen.findByText("Fees: Part paid")).toBeInTheDocument();
    expect(screen.getByTestId("fee-balance")).toHaveTextContent("₹1,000 owed");
    expect(screen.getByRole("list", { name: "Payments" })).toHaveTextContent("₹770 paid");
    expect(posts[0]?.body).toMatchObject({ amount: "770", mode: "upi", reference: "UTR123" });
  });

  it("reverses a payment with a reason, keeping the original", async () => {
    const posts: Call[] = [];
    const payment = { id: 3, invoice_id: 1, invoice_no: "INV/26-27/00001", kind: "payment", amount: "1770.00", entry_date: "2026-10-14", mode: "cheque", reference: "CHQ 12", note: null, reverses_id: null, reversed: false, by: "Asha Rao", at: "2026-10-14T06:00:00Z" };
    renderApp("/clients/100001", {
      user: staff,
      handlers: {
        ...quiet,
        "GET /api/clients/100001": () => jsonResponse(client),
        "GET /api/clients/100001/fees": () => jsonResponse(fees({ status: "paid", status_label: "Paid", balance: "0.00", invoices: [{ ...invoice, paid: "1770.00", balance: "0.00", status: "paid", status_label: "Paid" }], ledger: [payment] })),
        "POST /api/ledger/3/reverse": (c) => {
          posts.push(c);
          return jsonResponse(fees({ ledger: [{ ...payment, id: 4, kind: "reversal", mode: null, reference: null, note: "Cheque bounced", reverses_id: 3 }, { ...payment, reversed: true }] }), 201);
        },
      },
    });
    expect(await screen.findByText("Fees: Paid")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Record a payment/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reverse" }));
    const go = screen.getByRole("button", { name: "Reverse payment" });
    expect(go).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Why is it reversed?"), "Cheque bounced");
    await userEvent.click(go);
    const list = await screen.findByRole("list", { name: "Payments" });
    expect(list).toHaveTextContent("Reversal of ₹1,770 · INV/26-27/00001: “Cheque bounced”");
    expect(list).toHaveTextContent("· reversed");
    expect(screen.getByText("Fees: Due")).toBeInTheDocument();
    expect(posts[0]?.body).toEqual({ reason: "Cheque bounced" });
  });
});

describe("Adjustments and the overdue policy", () => {
  it("staff ask for a write-off, which waits for an Administrator", async () => {
    const posts: Call[] = [];
    renderApp("/clients/100001", {
      user: staff,
      handlers: {
        ...quiet,
        "GET /api/clients/100001": () => jsonResponse(client),
        "GET /api/clients/100001/fees": () => jsonResponse(fees()),
        "POST /api/invoices/1/adjustments": (c) => { posts.push(c); return jsonResponse(fees({ adjustments: [adjustment] }), 201); },
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Adjust INV/26-27/00001" }));
    const form = screen.getByRole("form", { name: "Adjustment to INV/26-27/00001" });
    expect(form).toHaveTextContent("Changes nothing until an Administrator approves it.");
    await userEvent.click(within(form).getByRole("combobox", { name: "Kind" }));
    await userEvent.click(await screen.findByRole("option", { name: "Write-off" }));
    await userEvent.type(within(form).getByLabelText("Adjustment (₹)"), "200");
    await userEvent.type(within(form).getByLabelText("Why?"), "Hardship");
    await userEvent.click(within(form).getByRole("button", { name: "Ask for approval" }));
    expect(await screen.findByRole("list", { name: "Adjustments" })).toHaveTextContent("Write-off · ₹200 · INV/26-27/00001 · waiting for an Administrator");
    expect(posts[0]?.body).toEqual({ kind: "write_off", amount: "200", reason: "Hardship" });
  });

  it("an Administrator approves an adjustment and sets the policy", async () => {
    const posts: Call[] = [];
    let waiting = [adjustment];
    renderApp("/setup/fees", {
      user: admin,
      handlers: {
        ...quiet,
        "GET /api/admin/fee-overrides": () => jsonResponse([]),
        "GET /api/admin/fee-adjustments": () => jsonResponse(waiting),
        "GET /api/admin/fee-policy": () => jsonResponse(policy),
        "GET /api/admin/tax-rates": () => jsonResponse([]),
        "POST /api/admin/fee-adjustments/7/decision": (c) => { posts.push(c); waiting = []; return jsonResponse({ ...adjustment, status: "approved" }); },
        "PUT /api/admin/fee-policy": (c) => { posts.push(c); return jsonResponse({ ...policy, mode: "block", limit: "500.00", updated_by: "The Boss" }); },
      },
    });
    expect(await screen.findByTestId("adjustment-request")).toHaveTextContent("Write-off of ₹200 on INV/26-27/00001");
    await userEvent.click(screen.getByRole("button", { name: "Approve write-off" }));
    expect(await screen.findAllByText("Nothing waiting.")).toHaveLength(2);
    const form = screen.getByRole("form", { name: "Overdue policy" });
    await userEvent.click(within(form).getByRole("combobox", { name: "When a client has overdue fees" }));
    await userEvent.click(await screen.findByRole("option", { name: /refuse/ }));
    const limit = within(form).getByLabelText(/Firm-wide limit/);
    await userEvent.clear(limit);
    await userEvent.type(limit, "500");
    await userEvent.click(within(form).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(posts.map((c) => c.body)).toEqual([{ approve: true }, { mode: "block", limit: "500" }]);
  });
});

describe("A client's overdue limit", () => {
  it("lets the owner set the client's own limit, with a reason", async () => {
    const puts: Call[] = [];
    renderApp("/clients/100001", {
      user: staff,
      handlers: {
        ...quiet,
        "GET /api/clients/100001": () => jsonResponse(client),
        "GET /api/clients/100001/fees": () => jsonResponse(fees({ overdue: "1180.00", over_limit: true })),
        "PUT /api/clients/100001/overdue-limit": (c) => { puts.push(c); return jsonResponse(fees({ overdue: "1180.00", overdue_limit: "2000.00", over_limit: false })); },
      },
    });
    const line = await screen.findByTestId("overdue-limit");
    expect(line).toHaveTextContent("Overdue: ₹1,180 · limit ₹0 (the firm's limit) · over the limit");
    await userEvent.click(within(line).getByRole("button", { name: "Change limit" }));
    const form = screen.getByRole("form", { name: "Overdue limit" });
    await userEvent.click(within(form).getByRole("combobox", { name: "Limit for this client" }));
    await userEvent.click(await screen.findByRole("option", { name: "Its own limit" }));
    await userEvent.type(within(form).getByLabelText("Overdue limit (₹)"), "2000");
    await userEvent.type(within(form).getByLabelText("Why the change?"), "Agreed credit");
    await userEvent.click(within(form).getByRole("button", { name: "Save limit" }));
    expect(await screen.findByText(/limit ₹2,000$/)).toBeInTheDocument();
    expect(puts[0]?.body).toEqual({ limit: "2000", reason: "Agreed credit" });
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
        "GET /api/admin/fee-adjustments": () => jsonResponse([]),
        "GET /api/admin/fee-policy": () => jsonResponse(policy),
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
    expect(await screen.findAllByText("Nothing waiting.")).toHaveLength(2);
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
        "GET /api/admin/fee-adjustments": () => jsonResponse([]),
        "GET /api/admin/fee-policy": () => jsonResponse(policy),
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
