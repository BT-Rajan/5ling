import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { admin, errorResponse, jsonResponse, renderApp, staff, stubScreen, type Call } from "../test-utils";

const noHealth = { "GET /api/health/ready": () => new Promise<Response>(() => undefined) };

const person = (over: Partial<Record<string, unknown>> = {}) => ({
  id: 2,
  email: "asha@firm.in",
  full_name: "Asha Rao",
  role: "staff",
  is_active: true,
  must_change_password: false,
  agreement_signed_on: null,
  last_login_at: null,
  created_at: "2026-10-08T00:00:00Z",
  ...over,
});
const boss = person({ id: 1, email: "boss@firm.in", full_name: "The Boss", role: "administrator" });

beforeEach(() => {
  stubScreen(true);
});

describe("Team screen", () => {
  it("is not available to Staff, whatever the address bar says", async () => {
    renderApp("/team", { user: staff, handlers: noHealth });
    expect(await screen.findByText("You do not have access to this page.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add person" })).not.toBeInTheDocument();
  });

  it("lists people with role and status", async () => {
    renderApp("/team", {
      user: admin,
      handlers: {
        ...noHealth,
        "GET /api/admin/users": () =>
          jsonResponse([boss, person(), person({ id: 3, full_name: "Ravi", email: "ravi@firm.in", is_active: false })]),
      },
    });
    expect(await screen.findByText("Asha Rao")).toBeInTheDocument();
    expect(screen.getByText("The Boss (you)")).toBeInTheDocument();
    expect(screen.getByText(/asha@firm.in · Staff · Active/)).toBeInTheDocument();
    expect(screen.getByText(/ravi@firm.in · Staff · Disabled/)).toBeInTheDocument();
  });

  it("shows a one-time temporary password after adding someone, and forgets it on close", async () => {
    const posts: Call[] = [];
    renderApp("/team", {
      user: admin,
      handlers: {
        ...noHealth,
        "GET /api/admin/users": () => jsonResponse([boss]),
        "POST /api/admin/users": (c) => {
          posts.push(c);
          return jsonResponse({ person: person(), temporary_password: "k7mp-x3qa-8hrw-n4te" }, 201);
        },
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Add person" }));
    await userEvent.type(screen.getByLabelText(/^full name/i), "Asha Rao");
    await userEvent.type(screen.getByLabelText(/^email/i), "asha@firm.in");
    await userEvent.click(screen.getByRole("button", { name: "Add person", hidden: false }));
    expect(await screen.findByTestId("temporary-password")).toHaveTextContent("k7mp-x3qa-8hrw-n4te");
    expect(posts[0]?.body).toEqual({ email: "asha@firm.in", full_name: "Asha Rao", role: "staff", agreement_signed_on: null });
    expect(posts[0]?.headers.get("X-CSRF-Token")).toBe("csrf-1");
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => {
      expect(screen.queryByTestId("temporary-password")).not.toBeInTheDocument();
    });
    expect(document.body).not.toHaveTextContent("k7mp-x3qa-8hrw-n4te");
  });

  it("asks for the agreement date when adding a consultant, and sends it", async () => {
    const posts: Call[] = [];
    renderApp("/team", {
      user: admin,
      handlers: {
        ...noHealth,
        "GET /api/admin/users": () => jsonResponse([boss]),
        "POST /api/admin/users": (c) => {
          posts.push(c);
          return jsonResponse({ person: person({ role: "consultant", agreement_signed_on: "2026-03-01" }), temporary_password: null }, 201);
        },
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Add person" }));
    expect(screen.queryByLabelText(/confidentiality agreement/i)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("combobox", { name: "Role" }));
    await userEvent.click(await screen.findByRole("option", { name: "Consultant" }));
    await userEvent.type(screen.getByLabelText(/^full name/i), "C Associates");
    await userEvent.type(screen.getByLabelText(/^email/i), "c@ca.in");
    const submit = screen.getByRole("button", { name: "Add person", hidden: false });
    expect(submit).toBeDisabled(); // no agreement date yet
    await userEvent.type(screen.getByLabelText(/confidentiality agreement/i), "2026-03-01");
    await userEvent.click(submit);
    expect(await screen.findByText(/Consultants sign in with an emailed link/)).toBeInTheDocument();
    expect(posts[0]?.body).toMatchObject({ role: "consultant", agreement_signed_on: "2026-03-01" });
    expect(screen.queryByTestId("temporary-password")).not.toBeInTheDocument();
  });

  it("shows the server's reason if adding fails", async () => {
    renderApp("/team", {
      user: admin,
      handlers: {
        ...noHealth,
        "GET /api/admin/users": () => jsonResponse([boss]),
        "POST /api/admin/users": () => errorResponse(409, "http_409", "Someone with that email already exists."),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Add person" }));
    await userEvent.type(screen.getByLabelText(/^full name/i), "Asha Rao");
    await userEvent.type(screen.getByLabelText(/^email/i), "asha@firm.in");
    await userEvent.click(screen.getByRole("button", { name: "Add person", hidden: false }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already exists");
  });

  it("disables someone only after confirmation, then refreshes the list", async () => {
    const posts: Call[] = [];
    let disabled = false;
    renderApp("/team", {
      user: admin,
      handlers: {
        ...noHealth,
        "GET /api/admin/users": () => jsonResponse([boss, person({ is_active: !disabled })]),
        "POST /api/admin/users/2/disable": (c) => {
          posts.push(c);
          disabled = true;
          return jsonResponse(person({ is_active: false }));
        },
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Actions for Asha Rao" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Disable" }));
    const dialog = await screen.findByRole("dialog", { name: "Disable Asha Rao?" });
    expect(posts).toHaveLength(0); // nothing sent yet
    await userEvent.click(within(dialog).getByRole("button", { name: "Disable" }));
    expect(await screen.findByText(/asha@firm.in · Staff · Disabled/)).toBeInTheDocument();
    expect(posts[0]?.headers.get("X-CSRF-Token")).toBe("csrf-1");
  });

  it("cancelling a confirmation sends nothing", async () => {
    const { calls } = renderApp("/team", {
      user: admin,
      handlers: { ...noHealth, "GET /api/admin/users": () => jsonResponse([boss, person()]) },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Actions for Asha Rao" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Disable" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });

  it("offers no actions on your own account", async () => {
    renderApp("/team", { user: admin, handlers: { ...noHealth, "GET /api/admin/users": () => jsonResponse([boss]) } });
    await userEvent.click(await screen.findByRole("button", { name: "Actions for The Boss" }));
    expect(await screen.findByText("No actions on your own account")).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Disable" })).not.toBeInTheDocument();
  });

  it("resets a password and shows the new temporary one once; consultants have no reset", async () => {
    renderApp("/team", {
      user: admin,
      handlers: {
        ...noHealth,
        "GET /api/admin/users": () =>
          jsonResponse([boss, person(), person({ id: 4, full_name: "C Associates", email: "c@ca.in", role: "consultant" })]),
        "POST /api/admin/users/2/reset-password": () => jsonResponse({ temporary_password: "n4te-8hrw-x3qa-k7mp" }),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Actions for C Associates" }));
    expect(screen.queryByRole("menuitem", { name: "Reset password" })).not.toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    await userEvent.click(await screen.findByRole("button", { name: "Actions for Asha Rao" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Reset password" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Reset password" }));
    expect(await screen.findByTestId("temporary-password")).toHaveTextContent("n4te-8hrw-x3qa-k7mp");
  });

  it("explains a failed load and lets you retry", async () => {
    let tries = 0;
    renderApp("/team", {
      user: admin,
      handlers: {
        ...noHealth,
        "GET /api/admin/users": () => {
          tries += 1;
          return tries === 1 ? errorResponse(500, "internal_error", "Something went wrong.") : jsonResponse([boss]);
        },
      },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong.");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("The Boss (you)")).toBeInTheDocument();
  });
});
