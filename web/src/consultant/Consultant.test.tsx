import { ThemeProvider } from "@mui/material/styles";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { admin, errorResponse, jsonResponse, mockApi, renderApp, stubScreen, type Call } from "../test-utils";
import type { User } from "../auth/AuthProvider";
import { theme } from "../theme";
import { ConsultantSignIn, takeToken } from "./ConsultantSignIn";

const quiet = { "GET /api/health/ready": () => new Promise<Response>(() => undefined), "GET /api/admin/audit/status": () => jsonResponse({ latest: null }) };
const ravi: User = { id: 7, email: "ravi@consult.in", full_name: "Ravi Consultant", role: "consultant", must_change_password: false, mfa_enabled: false };

const work = { id: 3, ticket: "TKT-000004", client_name: "Asha Rao", filing: "GSTR-1", period_label: "Apr 2026", due_date: "2026-05-11", work_due_date: "2026-05-08", note: "Prepare the return", stage_label: "Assigned", assigned_by: "The Boss", assigned_at: "2026-05-01T05:00:00Z" };

beforeEach(() => {
  stubScreen(true);
});

describe("Consultant sign-in", () => {
  it("asks for a link and shows the code the link will ask for", async () => {
    const posts: Call[] = [];
    renderApp("/", { user: null, handlers: { ...quiet, "POST /api/auth/consultant/start": (c) => { posts.push(c); return jsonResponse({ code: "482 913", minutes: 15 }); } } });
    await userEvent.click(await screen.findByRole("button", { name: "Consultant? Sign in with an emailed link" }));
    await userEvent.type(screen.getByLabelText(/Email/), "ravi@consult.in");
    await userEvent.click(screen.getByRole("button", { name: "Email me a link" }));
    expect(await screen.findByTestId("consultant-code")).toHaveTextContent("482 913");
    expect(posts[0]?.body).toEqual({ email: "ravi@consult.in" });
  });

  it("finishes with the token from the link and the code, then leaves the link behind", async () => {
    const posts: Call[] = [];
    mockApi({ handlers: { "POST /api/auth/consultant/finish": (c) => { posts.push(c); return errorResponse(401, "bad_code", "That link or code is not right, or the link has expired. Ask for a new link."); } } });
    window.history.replaceState(null, "", "/consultant/sign-in#t=abcdefghijklmnop");
    const token = takeToken();
    expect(window.location.hash).toBe("");
    render(<ThemeProvider theme={theme}><ConsultantSignIn token={token} /></ThemeProvider>);
    const go = screen.getByRole("button", { name: "Sign in" });
    expect(go).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Code"), "482 913");
    await userEvent.click(go);
    expect(await screen.findByText(/That link or code is not right/)).toBeInTheDocument();
    expect(posts[0]?.body).toEqual({ token: "abcdefghijklmnop", code: "482 913" });
  });
});

describe("Consultant work", () => {
  it("lists the consultant's work and opens one assignment", async () => {
    renderApp("/", {
      user: ravi,
      handlers: {
        ...quiet,
        "GET /api/consultant/assignments": () => jsonResponse([work]),
        "GET /api/consultant/assignments/3": () => jsonResponse({ ...work, documents: [{ id: 5, title: "Sales register", latest_version: 1, versions: [{ id: 50, version: 1, filename: "sales.pdf", content_type: "application/pdf", size_bytes: 10, uploaded_via: "portal", uploaded_by: null, uploaded_at: "2026-05-01T05:00:00Z", scan_status: "clean", scan_label: "Checked" }] }], allowed: "PDF", max_mb: 20, can_upload: true, can_submit: false, review: [] }),
      },
    });
    const list = await screen.findByRole("list", { name: "Your work" });
    await userEvent.click(within(list).getByRole("link", { name: /Asha Rao · GSTR-1 Apr 2026/ }));
    expect(await screen.findByRole("heading", { name: "Asha Rao · GSTR-1 Apr 2026" })).toBeInTheDocument();
    expect(screen.getByText("Prepare the return")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Versions of Sales register" })).toHaveTextContent("Version 1 · sales.pdf");
    expect(screen.getByRole("button", { name: "Upload your work" })).toBeInTheDocument();
  });

  it("shows the reviewer's comment and sends the work back for review", async () => {
    const posts: Call[] = [];
    const base = { ...work, stage_label: "Preparing", documents: [], allowed: "PDF", max_mb: 20, can_upload: true, can_submit: true, review: [{ action: "return", label: "Return to the preparer", note: "Input credit is wrong", by: "The Boss", at: "2026-05-02T05:00:00Z" }] };
    renderApp("/assignments/3", {
      user: ravi,
      handlers: {
        ...quiet,
        "GET /api/consultant/assignments/3": () => jsonResponse(base),
        "POST /api/consultant/assignments/3/submit": (c) => { posts.push(c); return jsonResponse({ ...base, stage_label: "In review", can_upload: false, can_submit: false, review: [...base.review, { action: "submit_for_review", label: "Send for review", note: "Fixed", by: "Ravi Consultant", at: "2026-05-03T05:00:00Z" }] }); },
      },
    });
    expect(await screen.findByRole("list", { name: "Review comments" })).toHaveTextContent("Returned to you: “Input credit is wrong”");
    await userEvent.type(screen.getByLabelText("Note for the reviewer (optional)"), "Fixed");
    await userEvent.click(screen.getByRole("button", { name: "Send for review" }));
    expect(await screen.findByText("Sent for review.")).toBeInTheDocument();
    expect(posts[0]?.body).toEqual({ note: "Fixed" });
    expect(screen.queryByRole("button", { name: "Upload your work" })).not.toBeInTheDocument();
    expect(screen.getByText("This work is with the reviewer, so it cannot be changed now.")).toBeInTheDocument();
  });

  it("says plainly when the work has ended", async () => {
    renderApp("/assignments/3", {
      user: ravi,
      handlers: { ...quiet, "GET /api/consultant/assignments/3": () => errorResponse(403, "no_access", "This work has ended, so you no longer have access to it.") },
    });
    expect(await screen.findByText("This work has ended, so you no longer have access to it.")).toBeInTheDocument();
  });
});

describe("Assignment panel", () => {
  it("assigns the work with a due date and a note", async () => {
    const posts: Call[] = [];
    renderApp("/tickets/TKT-000001", {
      user: admin,
      handlers: {
        ...quiet,
        "GET /api/tickets/TKT-000001": () => jsonResponse({ code: "TKT-000001", type: "filing", title: "GSTR-1 Apr 2026", client: { client_id: 100001, legal_name: "Asha Rao" }, created_at: "2026-04-01T00:00:00Z", cycle: { filing: { code: "GSTR-1", name: "Outward" }, period_label: "Apr 2026", period_start: "2026-04-01", period_end: "2026-04-30", due_date: "2026-05-11", alert_date: "2026-05-07", stage: "documents_received", stage_label: "Documents received", stage_changed_at: "2026-05-01T00:00:00Z", package_name: "GST", package_version: 1, calendar_version: 1, moves: [], extensions: [] }, task: null, history: [], can_edit: true }),
        "GET /api/tickets/TKT-000001/assignment": () => jsonResponse({ current: null, history: [] }),
        "GET /api/assignment-candidates": () => jsonResponse([{ id: 7, full_name: "Ravi Consultant", role: "consultant" }]),
        "POST /api/tickets/TKT-000001/assignment": (c) => {
          posts.push(c);
          const a = { id: 1, assignee: { id: 7, full_name: "Ravi Consultant", role: "consultant" }, work_due_date: "2026-05-08", note: "Prepare it", assigned_by: "The Boss", assigned_at: "2026-05-01T06:00:00Z", ended_at: null, end_reason: null, end_note: null };
          return jsonResponse({ current: a, history: [a] });
        },
      },
    });
    expect(await screen.findByText("Not assigned to anyone.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("combobox", { name: "Assign to" }));
    await userEvent.click(await screen.findByRole("option", { name: "Ravi Consultant · Consultant" }));
    await userEvent.type(screen.getByLabelText("Work due"), "2026-05-08");
    await userEvent.type(screen.getByLabelText("Note for them (optional)"), "Prepare it");
    await userEvent.click(screen.getByRole("button", { name: "Assign" }));
    expect(await screen.findByTestId("current-assignment")).toHaveTextContent("Ravi Consultant (Consultant)");
    expect(posts[0]?.body).toEqual({ assignee_id: 7, work_due_date: "2026-05-08", note: "Prepare it" });
  });
});
