import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { admin, errorResponse, jsonResponse, renderApp, staff, stubScreen, type Call } from "../test-utils";

const noHealth = { "GET /api/health/ready": () => new Promise<Response>(() => undefined) };

const passed = {
  id: 4,
  started_at: "2026-10-14T03:15:00Z",
  finished_at: "2026-10-14T03:15:01Z",
  trigger: "scheduled",
  requested_by_id: null,
  ok: true,
  entries_checked: 120,
  last_seq: 120,
  first_bad_seq: null,
  problem: null,
  problem_text: null,
};
const failed = {
  ...passed,
  id: 5,
  trigger: "manual",
  requested_by_id: 1,
  ok: false,
  entries_checked: 41,
  last_seq: 41,
  first_bad_seq: 42,
  problem: "hash_mismatch",
  problem_text: "This entry was changed after it was written.",
};

const entry = (seq: number, over: Record<string, unknown> = {}) => ({
  seq,
  occurred_at: "2026-10-14T09:30:00Z",
  event: "login.succeeded",
  actor_user_id: 1,
  actor_name: "The Boss",
  actor_role: "administrator",
  ip: "10.0.0.5",
  request_id: "r1",
  target_type: null,
  target_id: null,
  details: {},
  entry_hash: "a".repeat(64),
  ...over,
});

const base = {
  ...noHealth,
  "GET /api/admin/audit/status": () => jsonResponse({ latest: passed }),
  "GET /api/admin/audit/events": () => jsonResponse(["login.failed", "login.succeeded"]),
  "GET /api/admin/users": () => jsonResponse([{ id: 1, full_name: "The Boss" }, { id: 2, full_name: "Asha Rao" }]),
  "GET /api/admin/audit/entries?limit=50": () =>
    jsonResponse({ items: [entry(3), entry(2, { event: "login.failed", actor_name: null, actor_user_id: null })], next_before_seq: 2 }),
};

beforeEach(() => {
  stubScreen(true);
});

describe("Audit log screen", () => {
  it("is not available to Staff, and not in their menu", async () => {
    renderApp("/audit", { user: staff, handlers: noHealth });
    expect(await screen.findByText("You do not have access to this page.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Audit log" })).not.toBeInTheDocument();
  });

  it("shows the chain is intact and lists entries in plain words, India time", async () => {
    renderApp("/audit", { user: admin, handlers: base });
    expect(await screen.findByTestId("chain-status")).toHaveTextContent("Intact. 120 entries checked on 14 Oct 2026, 08:45:01 IST (daily check).");
    const list = await screen.findByRole("list", { name: "Audit entries" });
    expect(within(list).getByText("#3 · Signed in")).toBeInTheDocument();
    expect(within(list).getByText("#2 · Sign-in failed")).toBeInTheDocument();
    expect(within(list).getByText(/15:00:00 IST · Not signed in · 10.0.0.5/)).toBeInTheDocument();
  });

  it("filters by what happened and pages to older entries", async () => {
    const { calls } = renderApp("/audit", {
      user: admin,
      handlers: {
        ...base,
        "GET /api/admin/audit/entries?limit=50&event=login.failed": () =>
          jsonResponse({ items: [entry(9, { event: "login.failed" })], next_before_seq: 9 }),
        "GET /api/admin/audit/entries?limit=50&event=login.failed&before_seq=9": () =>
          jsonResponse({ items: [entry(5, { event: "login.failed" })], next_before_seq: null }),
      },
    });
    await screen.findByText("#3 · Signed in");
    await userEvent.click(screen.getByRole("combobox", { name: "What happened" }));
    await userEvent.click(await screen.findByRole("option", { name: "Sign-in failed" }));
    expect(await screen.findByText("#9 · Sign-in failed")).toBeInTheDocument();
    expect(screen.queryByText("#3 · Signed in")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show older entries" }));
    expect(await screen.findByText("#5 · Sign-in failed")).toBeInTheDocument();
    expect(screen.getByText("#9 · Sign-in failed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show older entries" })).not.toBeInTheDocument();
    expect(calls.some((c) => c.path.includes("before_seq=9"))).toBe(true);
  });

  it("checks on request and shows a failure plainly", async () => {
    const posts: Call[] = [];
    renderApp("/audit", {
      user: admin,
      handlers: {
        ...base,
        "POST /api/admin/audit/verify": (c) => {
          posts.push(c);
          return jsonResponse(failed);
        },
      },
    });
    await screen.findByTestId("chain-status");
    await userEvent.click(screen.getByRole("button", { name: "Check now" }));
    const status = await screen.findByText(/may have been tampered with/);
    expect(status.closest("[data-testid=chain-status]")).toHaveTextContent(
      "failed at entry 42: This entry was changed after it was written.",
    );
    expect(posts[0]?.headers.get("X-CSRF-Token")).toBe("csrf-1");
  });

  it("exports the filtered log as a CSV file", async () => {
    const posts: Call[] = [];
    const createObjectURL = vi.fn(() => "blob:audit");
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }));
    const clicked: HTMLAnchorElement[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this);
    });
    renderApp("/audit", {
      user: admin,
      handlers: {
        ...base,
        "GET /api/admin/audit/entries?limit=50&from_date=2026-10-01": () => jsonResponse({ items: [], next_before_seq: null }),
        "POST /api/admin/audit/export": (c) => {
          posts.push(c);
          return new Response("# Ledgerline audit log export\r\n", {
            headers: { "Content-Type": "text/csv", "Content-Disposition": 'attachment; filename="ledgerline-audit-20261014-1500.csv"' },
          });
        },
      },
    });
    await screen.findByText("#3 · Signed in");
    await userEvent.type(screen.getByLabelText("From"), "2026-10-01");
    expect(await screen.findByText("Nothing matches these filters.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    await waitFor(() => {
      expect(clicked).toHaveLength(1);
    });
    expect(posts[0]?.body).toEqual({ from_date: "2026-10-01" });
    expect(posts[0]?.headers.get("X-CSRF-Token")).toBe("csrf-1");
    expect(clicked[0]?.download).toBe("ledgerline-audit-20261014-1500.csv");
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    click.mockRestore();
  });

  it("says why an export was refused", async () => {
    renderApp("/audit", {
      user: admin,
      handlers: {
        ...base,
        "POST /api/admin/audit/export": () => errorResponse(422, "export_too_large", "Narrow the dates."),
      },
    });
    await screen.findByText("#3 · Signed in");
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    expect(await screen.findByText("Narrow the dates.")).toBeInTheDocument();
  });
});

describe("Tamper alarm", () => {
  it("warns every Administrator on every screen after a failed check", async () => {
    renderApp("/team", {
      user: admin,
      handlers: { ...base, "GET /api/admin/audit/status": () => jsonResponse({ latest: failed }), "GET /api/admin/users": () => jsonResponse([]) },
    });
    const alarm = await screen.findByText("The audit log may have been tampered with");
    expect(alarm.closest("[role=alert]")).toHaveTextContent("failed at entry 42");
    expect(screen.getByRole("link", { name: "Open audit log" })).toHaveAttribute("href", "/audit");
  });

  it("stays quiet when the last check passed", async () => {
    const { calls } = renderApp("/", { user: admin, handlers: base });
    await waitFor(() => {
      expect(calls.some((c) => c.path === "/api/admin/audit/status")).toBe(true);
    });
    expect(screen.queryByText("The audit log may have been tampered with")).not.toBeInTheDocument();
  });

  it("is not asked for by Staff", async () => {
    const { calls } = renderApp("/", { user: staff, handlers: noHealth });
    await screen.findByRole("heading", { level: 1 });
    expect(calls.some((c) => c.path.startsWith("/api/admin/"))).toBe(false);
  });
});

describe("Cycle generator alarm", () => {
  const failedRun = { id: 7, status: "failed", finished_at: "2026-10-14T20:00:00Z", clients_failed: 2, cycles_created: 40, error: "client 100004: ValueError" };

  it("warns the Administrator and runs the generator again on request", async () => {
    const posts: Call[] = [];
    renderApp("/", {
      user: admin,
      handlers: {
        ...base,
        "GET /api/admin/jobs/cycle-generator": () => jsonResponse({ latest: failedRun, recent: [failedRun] }),
        "POST /api/admin/jobs/cycle-generator/run": (c) => {
          posts.push(c);
          return jsonResponse({ ...failedRun, id: 8, status: "ok", clients_failed: 0, error: null });
        },
      },
    });
    const title = await screen.findByText("Some filing cycles were not created");
    const alert = title.closest("[role=alert]") as HTMLElement;
    expect(alert).toHaveTextContent("could not finish 2 clients (client 100004: ValueError)");
    await userEvent.click(within(alert).getByRole("button", { name: "Run again" }));
    await waitFor(() => {
      expect(screen.queryByText("Some filing cycles were not created")).not.toBeInTheDocument();
    });
    expect(posts[0]?.headers.get("X-CSRF-Token")).toBe("csrf-1");
  });

  it("says so if the run fails again", async () => {
    renderApp("/", {
      user: admin,
      handlers: {
        ...base,
        "GET /api/admin/jobs/cycle-generator": () => jsonResponse({ latest: failedRun, recent: [failedRun] }),
        "POST /api/admin/jobs/cycle-generator/run": () => jsonResponse({ ...failedRun, id: 9 }),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Run again" }));
    expect(await screen.findByText(/It failed again\./)).toBeInTheDocument();
  });
});
