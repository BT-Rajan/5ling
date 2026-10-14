import { ThemeProvider } from "@mui/material/styles";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { errorResponse, jsonResponse, mockApi, type Call } from "../test-utils";
import { theme } from "../theme";
import { PortalApp, type Portal } from "./PortalApp";

const portal: Portal = {
  client_name: "Asha Rao",
  client_id: 100001,
  ticket: "TKT-000004",
  filing_code: "GSTR-1",
  filing_name: "Outward supplies",
  period_label: "Sep 2026",
  due_date: "2026-10-12",
  stage_label: "Awaiting documents",
  expires_at: "2026-10-17T05:00:00Z",
  csrf_token: "portal-csrf",
};

function show(hash = "") {
  window.history.replaceState(null, "", `/portal${hash}`);
  return render(<ThemeProvider theme={theme}><PortalApp /></ThemeProvider>);
}

function gone(code: string, message: string, canRequestNew: boolean) {
  return () => new Response(JSON.stringify({ error: { code, message, can_request_new: canRequestNew } }), { status: 410, headers: { "Content-Type": "application/json" } });
}

describe("Client portal", () => {
  it("opens the link once, hides it from the address bar, and shows the one cycle", async () => {
    const posts: Call[] = [];
    mockApi({ handlers: { "POST /api/portal/open": (c) => { posts.push(c); return jsonResponse(portal); } } });
    show("#t=abcDEF123_-xyz");
    expect(await screen.findByRole("heading", { name: "Hello, Asha Rao" })).toBeInTheDocument();
    expect(posts).toHaveLength(1);
    expect(posts[0]?.body).toEqual({ token: "abcDEF123_-xyz" });
    expect(window.location.hash).toBe("");
    expect(screen.getByTestId("portal-stage")).toHaveTextContent("Awaiting documents");
    expect(screen.getByText("Mon 12 Oct 2026")).toBeInTheDocument();
    expect(screen.getByText("TKT-000004 · client ID 100001")).toBeInTheDocument();
  });

  it("explains a reused link and emails a new one to the address on file", async () => {
    const asks: Call[] = [];
    mockApi({
      handlers: {
        "POST /api/portal/open": gone("link_used", "This link has already been opened. Each link opens once, on one device.", true),
        "POST /api/portal/request-link": (c) => { asks.push(c); return jsonResponse({ sent_to: "a•••@example.in" }, 202); },
      },
    });
    show("#t=used-token");
    expect(await screen.findByText("This link has already been opened. Each link opens once, on one device.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Email me a new link" }));
    expect(await screen.findByText(/We have emailed a new link to a•••@example.in/)).toBeInTheDocument();
    expect(asks[0]?.body).toEqual({ token: "used-token" });
  });

  it("offers nothing more for finished work", async () => {
    mockApi({ handlers: { "POST /api/portal/open": gone("link_closed", "This work is finished, so the link no longer opens.", false) } });
    show("#t=old");
    expect(await screen.findByText("This work is finished, so the link no longer opens.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Email me a new link" })).not.toBeInTheDocument();
  });

  it("shows nothing without a link, and keeps a session on reload", async () => {
    mockApi({ handlers: { "GET /api/portal/me": () => errorResponse(401, "portal_session_ended", "Your private link has ended.") } });
    const first = show();
    expect(await screen.findByText("Open the private link from your email to see this page.")).toBeInTheDocument();
    first.unmount();

    const calls: Call[] = [];
    mockApi({
      handlers: {
        "GET /api/portal/me": () => jsonResponse(portal),
        "POST /api/portal/logout": (c) => { calls.push(c); return new Response(null, { status: 204 }); },
      },
    });
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Sign out on this device" }));
    expect(await screen.findByText(/You have signed out on this device/)).toBeInTheDocument();
    expect(calls[0]?.headers.get("X-CSRF-Token")).toBe("portal-csrf");
  });
});
