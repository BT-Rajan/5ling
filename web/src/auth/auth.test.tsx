import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { errorResponse, jsonResponse, renderApp, staff, stubScreen, type Call } from "../test-utils";

const noHealth = { "GET /api/health/ready": () => new Promise<Response>(() => undefined) };
const signedOut = { user: null, handlers: noHealth } as const;

beforeEach(() => {
  stubScreen(false);
});

async function fillAndSubmit(email: string, password: string) {
  await userEvent.type(screen.getByLabelText(/email/i), email);
  await userEvent.type(screen.getByLabelText(/password/i), password);
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("starting up", () => {
  it("shows neither the app nor the sign-in form while it is still finding out who you are", () => {
    renderApp("/", { handlers: { ...noHealth, "GET /api/auth/me": () => new Promise<Response>(() => undefined) } });
    expect(screen.getByRole("progressbar", { name: "Loading" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
  });

  it("offers to try again when the server cannot be reached", async () => {
    let tries = 0;
    renderApp("/", {
      handlers: {
        ...noHealth,
        "GET /api/auth/me": () => {
          tries += 1;
          if (tries === 1) throw new TypeError("offline");
          return jsonResponse({ user: staff, csrf_token: "c" });
        },
      },
    });
    expect(await screen.findByText("We could not reach Ledgerline")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("navigation", { name: "Main" })).toBeInTheDocument();
  });

  it("asks anonymous visitors to sign in, whichever page they asked for", async () => {
    renderApp("/clients", signedOut);
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByText(/No clients yet/)).not.toBeInTheDocument();
  });
});

describe("signing in", () => {
  it("has the right autofill hints for password managers", async () => {
    renderApp("/", signedOut);
    expect(await screen.findByLabelText(/email/i)).toHaveAttribute("autocomplete", "username");
    expect(screen.getByLabelText(/password/i)).toHaveAttribute("autocomplete", "current-password");
    expect(screen.getByLabelText(/password/i)).toHaveAttribute("type", "password");
  });

  it("signs in, shows the app on the page asked for, and then sends the CSRF token on changes", async () => {
    const calls: Call[] = [];
    let signedIn = false;
    renderApp("/clients", {
      user: null,
      handlers: {
        ...noHealth,
        "GET /api/auth/me": () =>
          signedIn ? jsonResponse({ user: staff, csrf_token: "csrf-xyz" }) : errorResponse(401, "http_401", "Sign in to continue."),
        "POST /api/auth/login": (c) => {
          calls.push(c);
          signedIn = true;
          return jsonResponse({ user: staff, csrf_token: "csrf-xyz" });
        },
        "POST /api/auth/logout": (c) => {
          calls.push(c);
          return new Response(null, { status: 204 });
        },
      },
    });
    await screen.findByRole("heading", { name: "Sign in" });
    await fillAndSubmit("asha@firm.in", "a long passphrase");
    expect(await screen.findByText(/No clients yet/)).toBeInTheDocument();
    expect(calls[0]?.body).toEqual({ email: "asha@firm.in", password: "a long passphrase" });

    await userEvent.click(screen.getByRole("button", { name: /account menu for asha rao/i }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(calls[1]?.headers.get("X-CSRF-Token")).toBe("csrf-xyz");
  });

  it("shows the server's message on a wrong password and clears the password field", async () => {
    renderApp("/", {
      user: null,
      handlers: {
        ...noHealth,
        "POST /api/auth/login": () => errorResponse(401, "http_401", "Email or password is incorrect, or the account is temporarily locked."),
      },
    });
    await screen.findByRole("heading", { name: "Sign in" });
    await fillAndSubmit("asha@firm.in", "wrong password!!");
    expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is incorrect");
    expect(screen.getByLabelText(/password/i)).toHaveValue("");
    expect(screen.getByLabelText(/email/i)).toHaveValue("asha@firm.in");
  });

  it("says so plainly when the network is down", async () => {
    renderApp("/", {
      user: null,
      handlers: {
        ...noHealth,
        "POST /api/auth/login": () => {
          throw new TypeError("offline");
        },
      },
    });
    await screen.findByRole("heading", { name: "Sign in" });
    await fillAndSubmit("asha@firm.in", "a long passphrase");
    expect(await screen.findByRole("alert")).toHaveTextContent("could not reach the server");
  });

  it("cannot be submitted empty and ignores double clicks while waiting", async () => {
    let logins = 0;
    renderApp("/", {
      user: null,
      handlers: {
        ...noHealth,
        "POST /api/auth/login": () => {
          logins += 1;
          return new Promise<Response>(() => undefined);
        },
      },
    });
    await screen.findByRole("heading", { name: "Sign in" });
    expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/email/i), "asha@firm.in");
    await userEvent.type(screen.getByLabelText(/password/i), "a long passphrase");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    // While waiting, the button is switched off, so a second tap cannot send a second request.
    expect(screen.getByRole("button", { name: /signing in/i })).toBeDisabled();
    expect(logins).toBe(1);
  });
});

describe("a session that ends while you work", () => {
  it("returns to the sign-in page when the server says 401", async () => {
    renderApp("/", {
      handlers: {
        "GET /api/health/ready": () => errorResponse(401, "http_401", "Sign in to continue."),
      },
    });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });
});

describe("first sign-in with a temporary password", () => {
  const temp = { ...staff, must_change_password: true };

  it("shows only the change-password screen, with no menu", async () => {
    renderApp("/clients", { user: temp, handlers: noHealth });
    expect(await screen.findByRole("heading", { name: "Choose a new password" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });

  it("checks the new password before enabling the button, then continues into the app", async () => {
    let changed = false;
    renderApp("/", {
      handlers: {
        ...noHealth,
        "GET /api/auth/me": () => jsonResponse({ user: changed ? staff : temp, csrf_token: "c1" }),
        "POST /api/auth/change-password": () => {
          changed = true;
          return new Response(null, { status: 204 });
        },
      },
    });
    await screen.findByRole("heading", { name: "Choose a new password" });
    const button = screen.getByRole("button", { name: "Change password" });
    await userEvent.type(screen.getByLabelText(/^temporary password/i), "k7mp-x3qa-8hrw-n4te");
    await userEvent.type(screen.getByLabelText(/^new password/i), "short");
    expect(button).toBeDisabled();
    await userEvent.clear(screen.getByLabelText(/^new password/i));
    await userEvent.type(screen.getByLabelText(/^new password/i), "a brand new long passphrase");
    await userEvent.type(screen.getByLabelText(/^repeat new password/i), "a different passphrase");
    expect(screen.getByText("The two passwords do not match.")).toBeInTheDocument();
    expect(button).toBeDisabled();
    await userEvent.clear(screen.getByLabelText(/^repeat new password/i));
    await userEvent.type(screen.getByLabelText(/^repeat new password/i), "a brand new long passphrase");
    expect(button).toBeEnabled();
    await userEvent.click(button);
    expect(await screen.findByRole("navigation", { name: "Main" })).toBeInTheDocument();
  });

  it("shows the server's reason when the new password is refused", async () => {
    renderApp("/", {
      user: temp,
      handlers: {
        ...noHealth,
        "POST /api/auth/change-password": () => errorResponse(422, "http_422", "That password is too easy to guess."),
      },
    });
    await screen.findByRole("heading", { name: "Choose a new password" });
    await userEvent.type(screen.getByLabelText(/^temporary password/i), "k7mp-x3qa-8hrw-n4te");
    await userEvent.type(screen.getByLabelText(/^new password/i), "passwordpassword");
    await userEvent.type(screen.getByLabelText(/^repeat new password/i), "passwordpassword");
    await userEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("too easy to guess");
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Change password" })).toBeEnabled();
    });
  });
});

describe("account menu", () => {
  it("shows who is signed in and their role", async () => {
    renderApp("/", { handlers: noHealth });
    await userEvent.click(await screen.findByRole("button", { name: /account menu for asha rao/i }));
    const menu = await screen.findByRole("menu");
    expect(within(menu).getByText("Asha Rao")).toBeInTheDocument();
    expect(within(menu).getByText("Staff")).toBeInTheDocument();
  });
});
