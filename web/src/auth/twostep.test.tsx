import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { admin, errorResponse, jsonResponse, renderApp, session, staff, stubScreen, type Call } from "../test-utils";

const noHealth = { "GET /api/health/ready": () => new Promise<Response>(() => undefined) };
const CHALLENGE = "c".repeat(30);

beforeEach(() => {
  stubScreen(false);
});

async function passwordStep() {
  await screen.findByRole("heading", { name: "Sign in" });
  await userEvent.type(screen.getByLabelText(/email/i), "asha@firm.in");
  await userEvent.type(screen.getByLabelText(/password/i), "a long passphrase");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

function codeStepApp(extra: Record<string, (c: Call) => Response | Promise<Response>> = {}) {
  return renderApp("/", {
    user: null,
    handlers: {
      ...noHealth,
      "POST /api/auth/login": () => jsonResponse({ status: "mfa_required", challenge: CHALLENGE }),
      ...extra,
    },
  });
}

describe("the code step", () => {
  it("asks for a 6-digit code with the right keyboard and autofill hints", async () => {
    codeStepApp();
    await passwordStep();
    const field = await screen.findByLabelText(/6-digit code/i);
    expect(field).toHaveAttribute("autocomplete", "one-time-code");
    expect(field).toHaveAttribute("inputmode", "numeric");
  });

  it("keeps you on the step, clears the field and shows the reason after a wrong code", async () => {
    codeStepApp({ "POST /api/auth/mfa/verify": () => errorResponse(401, "bad_code", "That code is not correct.") });
    await passwordStep();
    await userEvent.type(await screen.findByLabelText(/6-digit code/i), "000000");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That code is not correct.");
    expect(screen.getByLabelText(/6-digit code/i)).toHaveValue("");
    expect(screen.getByRole("heading", { name: "Two-step sign-in" })).toBeInTheDocument();
  });

  it("switches to a recovery code and back", async () => {
    const verifies: Call[] = [];
    codeStepApp({
      "POST /api/auth/mfa/verify": (c) => {
        verifies.push(c);
        return jsonResponse(session(staff, { recovery_codes_left: 9 }));
      },
    });
    await passwordStep();
    await userEvent.click(await screen.findByRole("button", { name: "Use a recovery code instead" }));
    const field = screen.getByLabelText(/recovery code/i);
    expect(field).toHaveAttribute("autocomplete", "off");
    await userEvent.type(field, "abcde-fghjk");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("navigation", { name: "Main" });
    expect(verifies[0]?.body).toEqual({ challenge: CHALLENGE, code: "abcde-fghjk" });
  });

  it("goes back to the password with the server's message when the sign-in has expired", async () => {
    codeStepApp({ "POST /api/auth/mfa/verify": () => errorResponse(401, "challenge_expired", "That sign-in has expired. Start again.") });
    await passwordStep();
    await userEvent.type(await screen.findByLabelText(/6-digit code/i), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByText("That sign-in has expired. Start again.")).toBeInTheDocument();
  });

  it("lets you start again from the code step", async () => {
    codeStepApp();
    await passwordStep();
    await userEvent.click(await screen.findByRole("button", { name: "Start again" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });
});

describe("first-time setup", () => {
  const info = {
    secret: "JBSW Y3DP EHPK 3PXP",
    otpauth_uri: "otpauth://totp/Ledgerline:asha%40firm.in?secret=JBSWY3DPEHPK3PXP&issuer=Ledgerline",
    qr: "data:image/svg+xml;charset=utf-8,%3Csvg%3E%3C/svg%3E",
  };
  const codes = Array.from({ length: 10 }, (_, i) => `abcd${i}-efgh${i}`);

  function setupApp(extra: Record<string, (c: Call) => Response | Promise<Response>> = {}) {
    return renderApp("/", {
      user: null,
      handlers: {
        ...noHealth,
        "POST /api/auth/login": () => jsonResponse({ status: "mfa_enrolment_required", challenge: CHALLENGE }),
        "POST /api/auth/mfa/enrol/start": () => jsonResponse(info),
        ...extra,
      },
    });
  }

  it("shows the picture, a link for this phone and the key to type by hand", async () => {
    const { calls } = setupApp();
    await passwordStep();
    expect(await screen.findByRole("heading", { name: "Set up two-step sign-in" })).toBeInTheDocument();
    const img = await screen.findByRole("img", { name: /QR code/i });
    expect(img).toHaveAttribute("src", info.qr);
    expect(screen.getByTestId("manual-key")).toHaveTextContent("JBSW Y3DP EHPK 3PXP");
    expect(screen.getByRole("link", { name: /open in your authenticator app/i })).toHaveAttribute("href", info.otpauth_uri);
    expect(calls.filter((c) => c.path === "/api/auth/mfa/enrol/start")).toHaveLength(1);
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });

  it("will not let you into the app until the recovery codes are acknowledged", async () => {
    setupApp({
      "POST /api/auth/mfa/enrol/confirm": () => jsonResponse(session(staff, { recovery_codes: codes })),
    });
    await passwordStep();
    await screen.findByRole("img", { name: /QR code/i });
    await userEvent.type(screen.getByLabelText(/6-digit code/i), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Turn on two-step sign-in" }));

    expect(await screen.findByRole("heading", { name: "Save your recovery codes" })).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Recovery codes" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(10);
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument(); // signed in on the server, not yet in the app
    const next = screen.getByRole("button", { name: "Continue" });
    expect(next).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: /i have saved these codes/i }));
    expect(next).toBeEnabled();
    await userEvent.click(next);
    expect(await screen.findByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("abcd0-efgh0"); // the codes are gone from the screen
  });

  it("shows the reason when the code is wrong and lets you try again", async () => {
    setupApp({ "POST /api/auth/mfa/enrol/confirm": () => errorResponse(401, "bad_code", "That code is not correct.") });
    await passwordStep();
    await screen.findByRole("img", { name: /QR code/i });
    await userEvent.type(screen.getByLabelText(/6-digit code/i), "000000");
    await userEvent.click(screen.getByRole("button", { name: "Turn on two-step sign-in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That code is not correct.");
    expect(screen.getByLabelText(/6-digit code/i)).toHaveValue("");
    expect(screen.getByRole("button", { name: "Turn on two-step sign-in" })).toBeDisabled(); // empty field
  });
});

describe("automatic sign-out after inactivity", () => {
  it("signs a Staff member out on screen and says why", async () => {
    const { calls } = renderApp("/", {
      user: staff,
      idleMs: 300,
      handlers: { ...noHealth, "POST /api/auth/logout": () => new Response(null, { status: 204 }) },
    });
    await screen.findByRole("navigation", { name: "Main" });
    expect(await screen.findByRole("heading", { name: "Sign in" }, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByText(/signed out after 20 minutes of inactivity/i)).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === "/api/auth/logout")).toBe(true);
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });

  it("applies to Administrators too", async () => {
    renderApp("/", { user: admin, idleMs: 300, handlers: { ...noHealth, "POST /api/auth/logout": () => new Response(null, { status: 204 }) } });
    await screen.findByRole("navigation", { name: "Main" });
    expect(await screen.findByRole("heading", { name: "Sign in" }, { timeout: 3000 })).toBeInTheDocument();
  });

  it("is postponed by activity", async () => {
    renderApp("/", { user: staff, idleMs: 600, handlers: { ...noHealth, "POST /api/auth/logout": () => new Response(null, { status: 204 }) } });
    await screen.findByRole("navigation", { name: "Main" });
    for (let i = 0; i < 4; i++) {
      await new Promise((r) => setTimeout(r, 250));
      act(() => {
        window.dispatchEvent(new Event("pointerdown"));
      });
    }
    // 1000ms have passed, longer than the 600ms limit, but we were active throughout.
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Sign in" }, { timeout: 3000 })).toBeInTheDocument();
  });

  it("shows the server's own message when it ends the session first", async () => {
    renderApp("/", {
      user: staff,
      handlers: {
        "GET /api/health/ready": () =>
          errorResponse(401, "session_idle", "You were signed out after 20 minutes of inactivity. Sign in again."),
      },
    });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByText(/20 minutes of inactivity/)).toBeInTheDocument();
  });

  it("an ordinary 401 does not claim inactivity", async () => {
    renderApp("/", {
      user: staff,
      handlers: { "GET /api/health/ready": () => errorResponse(401, "unauthenticated", "Sign in to continue.") },
    });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByText(/inactivity/)).not.toBeInTheDocument();
  });
});

describe("account security", () => {
  const open = async (left: number, handlers: Record<string, (c: Call) => Response | Promise<Response>> = {}) => {
    renderApp("/account", {
      user: staff,
      handlers: {
        ...noHealth,
        "GET /api/auth/me": () => jsonResponse(session(staff, { recovery_codes_left: left })),
        ...handlers,
      },
    });
    await screen.findByText(/Recovery codes left/);
  };

  it("shows how many recovery codes are left", async () => {
    await open(7);
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.queryByText(/running low/i)).not.toBeInTheDocument();
  });

  it("warns when they are running low or gone", async () => {
    await open(2);
    expect(screen.getByText(/running low on recovery codes/i)).toBeInTheDocument();
  });

  it("makes new codes after asking for the password, and refreshes the count", async () => {
    let left = 1;
    const posts: Call[] = [];
    await open(left, {
      "GET /api/auth/me": () => jsonResponse(session(staff, { recovery_codes_left: left })),
      "POST /api/auth/mfa/recovery-codes": (c) => {
        posts.push(c);
        left = 10;
        return jsonResponse({ recovery_codes: Array.from({ length: 10 }, (_, i) => `new${i}x-code${i}`) });
      },
    });
    await userEvent.click(screen.getByRole("button", { name: "Make new recovery codes" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/password/i), "a long passphrase");
    await userEvent.click(within(dialog).getByRole("button", { name: "Make new codes" }));
    expect(await within(dialog).findByRole("list", { name: "Recovery codes" })).toBeInTheDocument();
    expect(posts[0]?.body).toEqual({ current_password: "a long passphrase" });
    expect(posts[0]?.headers.get("X-CSRF-Token")).toBe("csrf-1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Done" }));
    await waitFor(() => {
      expect(screen.getByText("10")).toBeInTheDocument();
    });
    expect(document.body).not.toHaveTextContent("new0x-code0");
  });

  it("shows the reason if the password is wrong", async () => {
    await open(5, { "POST /api/auth/mfa/recovery-codes": () => errorResponse(400, "bad_password", "Your current password is not correct.") });
    await userEvent.click(screen.getByRole("button", { name: "Make new recovery codes" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/password/i), "not the password");
    await userEvent.click(within(dialog).getByRole("button", { name: "Make new codes" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("current password is not correct");
  });
});
