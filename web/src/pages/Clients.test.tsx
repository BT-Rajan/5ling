import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { User } from "../auth/AuthProvider";
import { admin, errorResponse, jsonResponse, renderApp, staff, stubScreen, type Call } from "../test-utils";

const quiet = { "GET /api/health/ready": () => new Promise<Response>(() => undefined), "GET /api/admin/audit/status": () => jsonResponse({ latest: null }) };

const options = {
  owners: [{ id: 2, full_name: "Asha Rao" }],
  packages: [{ id: 5, name: "GST Monthly", version: 1, fee: "1500.00", billing_frequency: "monthly" }],
};

const saved = {
  client_id: 100001,
  kind: "individual",
  legal_name: "Ravi Kumar",
  pan_masked: "AB•••••34F",
  gstin: null,
  status: "active",
  owner: { id: 2, full_name: "Asha Rao" },
  package: { id: 5, name: "GST Monthly", version: 1 },
  contacts: [{ id: 11, name: "Ravi Kumar", email: "ravi@x.in", phone: "+919876543210", is_primary: true, consent: { email: true, whatsapp: false, sms: false } }],
  created_at: "2026-10-14T06:00:00Z",
};

beforeEach(() => {
  stubScreen(true);
});

async function fillIndividual() {
  await userEvent.type(await screen.findByLabelText(/^Legal name/), "Ravi Kumar");
  await userEvent.type(screen.getByLabelText("PAN"), "abcpe1234f");
  await userEvent.click(screen.getByRole("combobox", { name: /Package/ }));
  await userEvent.click(await screen.findByRole("option", { name: /GST Monthly/ }));
  await userEvent.type(screen.getByLabelText(/^Contact 1 name/), "Ravi Kumar");
  await userEvent.type(screen.getByLabelText(/^Contact 1 email/), "ravi@x.in");
  await userEvent.type(screen.getByLabelText("Contact 1 mobile"), "98765 43210");
}

describe("Adding a client", () => {
  it("lets Staff add a client they own and shows the new client ID", async () => {
    const posts: Call[] = [];
    renderApp("/clients/new", {
      user: staff,
      handlers: {
        ...quiet,
        "GET /api/clients/options": () => jsonResponse(options),
        "POST /api/clients": (c) => { posts.push(c); return jsonResponse(saved, 201); },
        "GET /api/clients/100001": () => jsonResponse(saved),
      },
    });
    expect(await screen.findByDisplayValue("Asha Rao (you)")).toBeDisabled();
    expect(screen.queryByLabelText("GSTIN")).not.toBeInTheDocument();
    await fillIndividual();
    await userEvent.click(screen.getByRole("switch", { name: "Agrees to WhatsApp" }));
    await userEvent.click(screen.getByRole("button", { name: "Save client" }));

    expect(await screen.findByTestId("client-id")).toHaveTextContent("100001");
    expect(screen.getByText("Client saved. Its client ID is 100001.")).toBeInTheDocument();
    expect(posts[0]?.body).toEqual({
      kind: "individual",
      legal_name: "Ravi Kumar",
      pan: "ABCPE1234F",
      gstin: null,
      owner_id: 2,
      package_id: 5,
      contacts: [{ name: "Ravi Kumar", email: "ravi@x.in", phone: "98765 43210", is_primary: true, whatsapp: true, sms: false }],
    });
    expect(posts[0]?.headers.get("X-CSRF-Token")).toBe("csrf-1");
  });

  it("points to the existing client when the PAN is already on file", async () => {
    renderApp("/clients/new", {
      user: admin,
      handlers: {
        ...quiet,
        "GET /api/clients/options": () => jsonResponse(options),
        "POST /api/clients": () =>
          jsonResponse(
            { error: { code: "duplicate_client", message: "Client 100001 (Ravi Kumar) already has this PAN.", request_id: "r", existing: { client_id: 100001, matched: "pan", can_open: true, legal_name: "Ravi Kumar" } } },
            409,
          ),
      },
    });
    await userEvent.click(await screen.findByRole("combobox", { name: /Owner/ }));
    await userEvent.click(await screen.findByRole("option", { name: "Asha Rao" }));
    await fillIndividual();
    await userEvent.click(screen.getByRole("button", { name: "Save client" }));
    const alert = await screen.findByText("Client 100001 (Ravi Kumar) already has this PAN.");
    expect(within(alert.closest("[role=alert]") as HTMLElement).getByRole("link", { name: "Open client 100001" })).toHaveAttribute("href", "/clients/100001");
  });

  it("lists what is wrong in plain words", async () => {
    renderApp("/clients/new", {
      user: staff,
      handlers: {
        ...quiet,
        "GET /api/clients/options": () => jsonResponse(options),
        "POST /api/clients": () =>
          jsonResponse(
            { error: { code: "validation_error", message: "Some fields are not valid.", fields: [{ where: ["body", "pan"], problem: "Value error, That is not a valid PAN. It should look like ABCPE1234F.", type: "value_error" }, { where: ["body", "contacts", 0, "email"], problem: "value is not a valid email address", type: "value_error" }] } },
            422,
          ),
      },
    });
    await fillIndividual();
    await userEvent.click(screen.getByRole("button", { name: "Save client" }));
    expect(await screen.findByText("PAN: That is not a valid PAN. It should look like ABCPE1234F.")).toBeInTheDocument();
    expect(screen.getByText("Email: value is not a valid email address")).toBeInTheDocument();
  });

  it("asks for a GSTIN only for businesses", async () => {
    renderApp("/clients/new", { user: staff, handlers: { ...quiet, "GET /api/clients/options": () => jsonResponse(options) } });
    await userEvent.click(await screen.findByRole("radio", { name: "Business" }));
    expect(screen.getByLabelText("GSTIN")).toBeInTheDocument();
  });

  it("is not open to consultants", async () => {
    const consultant: User = { id: 9, email: "c@ca.in", full_name: "Con", role: "consultant", must_change_password: false, mfa_enabled: false };
    renderApp("/clients/new", { user: consultant, handlers: quiet });
    expect(await screen.findByText("You do not have access to this page.")).toBeInTheDocument();
  });
});

describe("A client's page", () => {
  it("masks the PAN until asked, and records consent changes", async () => {
    const puts: Call[] = [];
    renderApp("/clients/100001", {
      user: staff,
      handlers: {
        ...quiet,
        "GET /api/clients/100001": () => jsonResponse(saved),
        "POST /api/clients/100001/pan": () => jsonResponse({ pan: "ABCPE1234F" }),
        "PUT /api/clients/100001/contacts/11/consent": (c) => {
          puts.push(c);
          return jsonResponse({ ...saved.contacts[0], consent: { email: true, whatsapp: false, sms: true } });
        },
      },
    });
    expect(await screen.findByTestId("pan")).toHaveTextContent("AB•••••34F");
    await userEvent.click(screen.getByRole("button", { name: "Show" }));
    expect(await screen.findByText("ABCPE1234F")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("switch", { name: "SMS for Ravi Kumar" }));
    expect(await screen.findByRole("switch", { name: "SMS for Ravi Kumar" })).toBeChecked();
    expect(puts[0]?.body).toEqual({ channel: "sms", granted: true });
  });

  it("says so when the client belongs to someone else", async () => {
    renderApp("/clients/100002", { user: staff, handlers: { ...quiet, "GET /api/clients/100002": () => errorResponse(403, "http_403", "You do not have access to this record.") } });
    expect(await screen.findByText("You do not have access to this record.")).toBeInTheDocument();
  });

  it("shows its history and records a change with a reason", async () => {
    const patches: Call[] = [];
    let current = saved;
    renderApp("/clients/100001", {
      user: admin,
      handlers: {
        ...quiet,
        "GET /api/clients/100001": () => jsonResponse(current),
        "GET /api/clients/options": () => jsonResponse({ owners: [{ id: 2, full_name: "Asha Rao" }, { id: 3, full_name: "Bina Shah" }], packages: options.packages }),
        "GET /api/clients/100001/history": () =>
          jsonResponse(
            current === saved
              ? []
              : [{ id: 1, field: "owner", old: "Asha Rao", new: "Bina Shah", reason: "Asha on leave", changed_by: { id: 1, full_name: "The Boss" }, changed_at: "2026-10-14T06:30:00Z" }],
          ),
        "PATCH /api/clients/100001": (c) => {
          patches.push(c);
          current = { ...saved, owner: { id: 3, full_name: "Bina Shah" } };
          return jsonResponse(current);
        },
      },
    });
    expect(await screen.findByText("No changes since the client was added.")).toBeInTheDocument();
    const save = screen.getByRole("button", { name: "Save change" });
    expect(save).toBeDisabled();
    await userEvent.click(screen.getByRole("combobox", { name: "New owner" }));
    await userEvent.click(await screen.findByRole("option", { name: "Bina Shah" }));
    expect(save).toBeDisabled(); // no reason yet
    await userEvent.type(screen.getByLabelText(/^Reason/), "Asha on leave");
    await userEvent.click(save);
    expect(await screen.findByText("Saved, and added to the history.")).toBeInTheDocument();
    expect(patches[0]?.body).toEqual({ reason: "Asha on leave", owner_id: 3 });
    const history = await screen.findByRole("list", { name: "History" });
    expect(history).toHaveTextContent("Owner: Asha Rao → Bina Shah");
    expect(history).toHaveTextContent("The Boss · “Asha on leave”");
  });

  it("tells Staff that only the Administrator reopens a closed client", async () => {
    renderApp("/clients/100001", {
      user: staff,
      handlers: { ...quiet, "GET /api/clients/100001": () => jsonResponse({ ...saved, status: "closed" }), "GET /api/clients/100001/history": () => jsonResponse([]), "GET /api/clients/options": () => jsonResponse(options) },
    });
    expect(await screen.findByText("This client is closed. Only the Administrator can reopen it.")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "New status" })).toHaveAttribute("aria-disabled", "true");
  });
});

describe("The client list", () => {
  const row = (id: number, name: string, over: Record<string, unknown> = {}) => ({
    client_id: id, kind: "individual", legal_name: name, pan_masked: "AB•••••34F", gstin: null, status: "active",
    owner: { id: 2, full_name: "Asha Rao" }, package: { id: 5, name: "GST Monthly", version: 1 }, ...over,
  });

  it("searches, filters and pages, with PANs masked", async () => {
    const calls: Call[] = [];
    const pageOne = { items: Array.from({ length: 50 }, (_, i) => row(100001 + i, `Client ${String(i + 1).padStart(2, "0")}`)), total: 51, offset: 0, limit: 50 };
    renderApp("/clients", {
      user: admin,
      handlers: {
        ...quiet,
        "GET /api/clients/options": () => jsonResponse(options),
        "GET /api/clients?limit=50&offset=0": (c) => { calls.push(c); return jsonResponse(pageOne); },
        "GET /api/clients?limit=50&offset=50": () => jsonResponse({ items: [row(100051, "Client 51")], total: 51, offset: 50, limit: 50 }),
        "GET /api/clients?limit=50&offset=0&q=ravi": () => jsonResponse({ items: [row(100009, "Ravi Kumar", { status: "paused" })], total: 1, offset: 0, limit: 50 }),
        "GET /api/clients?limit=50&offset=0&q=ravi&status=paused": () => jsonResponse({ items: [row(100009, "Ravi Kumar", { status: "paused" })], total: 1, offset: 0, limit: 50 }),
      },
    });
    const list = await screen.findByRole("list", { name: "Clients" });
    expect(within(list).getByRole("link", { name: /100001 · Client 01/ })).toHaveAttribute("href", "/clients/100001");
    expect(list).toHaveTextContent("AB•••••34F");
    await userEvent.click(screen.getByRole("button", { name: "Show more" }));
    expect(await screen.findByText("100051 · Client 51")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument();

    await userEvent.type(screen.getByLabelText(/^Search/), "ravi");
    expect(await screen.findByText("100009 · Ravi Kumar")).toBeInTheDocument();
    expect(screen.getByText(/Paused/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("combobox", { name: "Status" }));
    await userEvent.click(await screen.findByRole("option", { name: "Paused" }));
    expect(await screen.findByText("1 client")).toBeInTheDocument();
  });

  it("does not offer an owner filter to Staff", async () => {
    renderApp("/clients", {
      user: staff,
      handlers: { ...quiet, "GET /api/clients/options": () => jsonResponse(options), "GET /api/clients?limit=50&offset=0": () => jsonResponse({ items: [], total: 0, offset: 0, limit: 50 }) },
    });
    expect(await screen.findByText("You have no clients yet.")).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Owner" })).not.toBeInTheDocument();
  });
});
