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
  pan: "ABCPE1234F",
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
        "PUT /api/clients/100001/contacts/11/consent": (c) => {
          puts.push(c);
          return jsonResponse({ ...saved.contacts[0], consent: { email: true, whatsapp: false, sms: true } });
        },
      },
    });
    expect(await screen.findByTestId("pan")).toHaveTextContent("AB•••••34F");
    await userEvent.click(screen.getByRole("button", { name: "Show" }));
    expect(screen.getByTestId("pan")).toHaveTextContent("ABCPE1234F");
    await userEvent.click(screen.getByRole("switch", { name: "SMS for Ravi Kumar" }));
    expect(await screen.findByRole("switch", { name: "SMS for Ravi Kumar" })).toBeChecked();
    expect(puts[0]?.body).toEqual({ channel: "sms", granted: true });
  });

  it("says so when the client belongs to someone else", async () => {
    renderApp("/clients/100002", { user: staff, handlers: { ...quiet, "GET /api/clients/100002": () => errorResponse(403, "http_403", "You do not have access to this record.") } });
    expect(await screen.findByText("You do not have access to this record.")).toBeInTheDocument();
  });

  it("can be opened by its client ID", async () => {
    renderApp("/clients", { user: staff, handlers: { ...quiet, "GET /api/clients/100001": () => jsonResponse(saved) } });
    await userEvent.type(await screen.findByLabelText("Client ID"), "100001");
    await userEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(await screen.findByTestId("client-id")).toHaveTextContent("100001");
  });
});
