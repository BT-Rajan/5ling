import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { admin, errorResponse, jsonResponse, renderApp, staff, stubScreen, type Call } from "../test-utils";

const noHealth = { "GET /api/health/ready": () => new Promise<Response>(() => undefined), "GET /api/admin/audit/status": () => jsonResponse({ latest: null }) };

const calendar = (over: Record<string, unknown> = {}) => ({
  id: 7,
  version: 1,
  status: "published",
  frequency: "monthly",
  period_basis: "financial_year",
  due: { months_after: 1, day: 11, overrides: {} },
  lead_days: null,
  holiday_rule: "earlier_working_day",
  notes: null,
  created_at: "2026-10-01T00:00:00Z",
  created_by_id: 1,
  published_at: "2026-10-01T04:30:00Z",
  published_by_id: 1,
  ...over,
});

const gstr1 = {
  id: 3,
  code: "GSTR-1",
  name: "Outward supplies",
  description: null,
  default_checklist: [{ label: "Sales register", required: true }],
  is_active: true,
  current: calendar(),
  draft: null,
};

beforeEach(() => {
  stubScreen(true);
});

describe("Setup", () => {
  it("collects Team, Filing calendars and Packages for the Administrator only", async () => {
    const first = renderApp("/setup", { user: staff, handlers: noHealth });
    expect(await screen.findByText("You do not have access to this page.")).toBeInTheDocument();
    first.unmount();
    renderApp("/setup", { user: admin, handlers: noHealth });
    for (const name of [/Team/, /Filing calendars/, /Packages/]) {
      expect(await screen.findByRole("link", { name })).toBeInTheDocument();
    }
  });

  it("keeps the old Team address working", async () => {
    renderApp("/team", { user: admin, handlers: { ...noHealth, "GET /api/admin/users": () => jsonResponse([]) } });
    expect(await screen.findByRole("button", { name: "Add person" })).toBeInTheDocument();
  });
});

describe("Filing calendars", () => {
  it("lists filing types with their calendar in plain words", async () => {
    renderApp("/setup/filings", {
      user: admin,
      handlers: {
        ...noHealth,
        "GET /api/admin/filings": () =>
          jsonResponse([gstr1, { ...gstr1, id: 4, code: "TDS 24Q", name: "TDS", current: null, draft: calendar({ status: "draft" }), is_active: false }]),
      },
    });
    const list = await screen.findByRole("list", { name: "Filing types" });
    expect(within(list).getByText("Monthly · due 11th of the month after the period · version 1")).toBeInTheDocument();
    expect(within(list).getByText("No calendar published yet")).toBeInTheDocument();
    expect(within(list).getByText("Draft")).toBeInTheDocument();
    expect(within(list).getByText("Retired")).toBeInTheDocument();
  });

  it("saves a quarterly calendar with a Q4 exception and publishes it", async () => {
    const calls: Call[] = [];
    let detail: Record<string, unknown> = { ...gstr1, current: null, versions: [] };
    renderApp("/setup/filings/3", {
      user: admin,
      handlers: {
        ...noHealth,
        "GET /api/admin/filings/3": () => jsonResponse(detail),
        "PUT /api/admin/filings/3/calendar/draft": (c) => {
          calls.push(c);
          return jsonResponse(calendar({ status: "draft", published_at: null }));
        },
        "POST /api/admin/filings/3/calendar/publish": (c) => {
          calls.push(c);
          detail = { ...detail, current: calendar(), versions: [calendar()] };
          return jsonResponse(calendar());
        },
      },
    });
    expect(await screen.findByText("Saving starts version 1.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("combobox", { name: "Frequency" }));
    await userEvent.click(await screen.findByRole("option", { name: "Quarterly" }));
    const months = screen.getByLabelText("Due: months after the period");
    await userEvent.clear(months);
    await userEvent.type(months, "1");
    const day = screen.getByLabelText("Due: day of that month");
    await userEvent.clear(day);
    await userEvent.type(day, "31");
    await userEvent.click(screen.getByRole("button", { name: "Add a period" }));
    await userEvent.click(screen.getByRole("combobox", { name: "Period" }));
    await userEvent.click(await screen.findByRole("option", { name: "Q4" }));
    const exceptionMonths = screen.getByLabelText("Months after");
    await userEvent.clear(exceptionMonths);
    await userEvent.type(exceptionMonths, "2");
    await userEvent.type(screen.getByLabelText("Alert lead time (days)"), "10");
    await userEvent.click(screen.getByRole("button", { name: "Save and publish" }));

    expect(await screen.findByText(/Calendar version 1 published/)).toBeInTheDocument();
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual(["PUT /api/admin/filings/3/calendar/draft", "POST /api/admin/filings/3/calendar/publish"]);
    expect(calls[0]?.body).toEqual({
      frequency: "quarterly",
      period_basis: "financial_year",
      due: { months_after: 1, day: 31, overrides: { Q4: { months_after: 2, day: 31 } } },
      lead_days: 10,
      holiday_rule: "earlier_working_day",
      notes: null,
    });
    expect(calls[0]?.headers.get("X-CSRF-Token")).toBe("csrf-1");
    expect(await screen.findByRole("list", { name: "Calendar versions" })).toHaveTextContent("Version 1 · published");
  });

  it("shows why a calendar was refused", async () => {
    renderApp("/setup/filings/3", {
      user: admin,
      handlers: {
        ...noHealth,
        "GET /api/admin/filings/3": () => jsonResponse({ ...gstr1, versions: [calendar()] }),
        "PUT /api/admin/filings/3/calendar/draft": () => errorResponse(422, "validation_error", "Some fields are not valid."),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Save draft" }));
    expect(await screen.findByText("Some fields are not valid.")).toBeInTheDocument();
  });
});

describe("Packages", () => {
  const empty = {
    id: 9,
    name: "GST Monthly",
    description: null,
    is_active: true,
    versions: [{ id: 20, version: 1, status: "draft", fee: null, billing_frequency: null, notes: null, filings: [], published_at: null }],
  };

  it("lists packages with what they cost and contain", async () => {
    renderApp("/setup/packages", {
      user: admin,
      handlers: {
        ...noHealth,
        "GET /api/admin/packages": () =>
          jsonResponse([
            { id: 9, name: "GST Monthly", description: null, is_active: true, current_version: 2, current_fee: "150000.00", current_billing_frequency: "monthly", current_filings: 2, has_draft: true },
            { id: 10, name: "Starter", description: null, is_active: true, current_version: null, current_fee: null, current_billing_frequency: null, current_filings: 0, has_draft: true },
          ]),
      },
    });
    const list = await screen.findByRole("list", { name: "Packages" });
    expect(within(list).getByText("₹1,50,000 monthly · 2 filings · version 2")).toBeInTheDocument();
    expect(within(list).getByText("Not published yet")).toBeInTheDocument();
  });

  it("builds a draft from a filing's checklist, then publishes it", async () => {
    const calls: Call[] = [];
    let detail: unknown = empty;
    renderApp("/setup/packages/9", {
      user: admin,
      handlers: {
        ...noHealth,
        "GET /api/admin/packages/9": () => jsonResponse(detail),
        "GET /api/admin/filings": () => jsonResponse([gstr1]),
        "PUT /api/admin/packages/9/draft": (c) => {
          calls.push(c);
          return jsonResponse({ ...empty.versions[0], fee: "1500.00", billing_frequency: "monthly" });
        },
        "POST /api/admin/packages/9/publish": (c) => {
          calls.push(c);
          const published = { id: 20, version: 1, status: "published", fee: "1500.00", billing_frequency: "monthly", notes: null, filings: [{ filing_id: 3, code: "GSTR-1", name: "Outward supplies", checklist: [] }], published_at: "2026-10-14T06:00:00Z" };
          detail = { ...empty, versions: [published] };
          return jsonResponse(published);
        },
      },
    });
    expect(await screen.findByText(/No filings yet/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Fee (₹)"), "1,500");
    await userEvent.click(screen.getByRole("combobox", { name: "Billed" }));
    await userEvent.click(await screen.findByRole("option", { name: "Monthly" }));
    await userEvent.click(screen.getByRole("combobox", { name: "Add a filing" }));
    await userEvent.click(await screen.findByRole("option", { name: "GSTR-1 · Outward supplies" }));
    // The filing type's checklist comes with it, and can be changed for this package.
    expect(screen.getByLabelText("Document 1")).toHaveValue("Sales register");
    await userEvent.click(screen.getByRole("button", { name: "Add document" }));
    await userEvent.type(screen.getByLabelText("Document 2"), "Purchase register");
    await userEvent.click(screen.getByRole("button", { name: "Save and publish" }));

    expect(await screen.findByText(/Version 1 published/)).toBeInTheDocument();
    expect(calls[0]?.body).toEqual({
      fee: "1500",
      billing_frequency: "monthly",
      notes: null,
      filings: [{ filing_id: 3, checklist: [{ label: "Sales register", required: true }, { label: "Purchase register", required: true }] }],
    });
    expect(calls[1]?.method).toBe("POST");
    expect(await screen.findByRole("list", { name: "Package versions" })).toHaveTextContent("₹1,500 monthly · GSTR-1");
  });

  it("explains why a package cannot be published yet, keeping the saved draft", async () => {
    renderApp("/setup/packages/9", {
      user: admin,
      handlers: {
        ...noHealth,
        "GET /api/admin/packages/9": () => jsonResponse(empty),
        "GET /api/admin/filings": () => jsonResponse([]),
        "PUT /api/admin/packages/9/draft": () => jsonResponse(empty.versions[0]),
        "POST /api/admin/packages/9/publish": () => errorResponse(422, "cannot_publish", "Set a fee. Add at least one filing."),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Save and publish" }));
    expect(await screen.findByText("Draft version 1 saved, but not published: Set a fee. Add at least one filing.")).toBeInTheDocument();
  });

  it("says editing a published package starts a new version", async () => {
    const published = { id: 20, version: 1, status: "published", fee: "900.00", billing_frequency: "yearly", notes: null, filings: [], published_at: "2026-10-14T06:00:00Z" };
    renderApp("/setup/packages/9", {
      user: admin,
      handlers: { ...noHealth, "GET /api/admin/packages/9": () => jsonResponse({ ...empty, versions: [published] }), "GET /api/admin/filings": () => jsonResponse([]) },
    });
    expect(await screen.findByText("Saving starts version 2; version 1 stays as it is.")).toBeInTheDocument();
    expect(screen.getByLabelText("Fee (₹)")).toHaveValue("900.00");
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Discard draft" })).not.toBeInTheDocument();
    });
  });
});

describe("Due-date preview", () => {
  const preview = (year: number) => ({
    year,
    year_label: `FY ${String(year)}-${String((year + 1) % 100)}`,
    lead_days: 4,
    lead_days_is_default: true,
    rows: [
      { key: "M7", label: `Oct ${String(year)}`, period_start: "", period_end: "", due_date: `${String(year)}-11-11`, due_moved_from: null, alert_date: `${String(year)}-11-05`, alert_moved_from: `${String(year)}-11-07` },
    ],
  });

  it("shows a full year of dates for the rule being edited, before saving", async () => {
    const posts: Call[] = [];
    renderApp("/setup/filings/3", {
      user: admin,
      handlers: {
        ...noHealth,
        "GET /api/admin/filings/3": () => jsonResponse({ ...gstr1, versions: [calendar()] }),
        "POST /api/admin/filings/preview": (c) => { posts.push(c); return jsonResponse(preview(2026)); },
        "POST /api/admin/filings/preview?year=2027": (c) => { posts.push(c); return jsonResponse(preview(2027)); },
      },
    });
    const table = await screen.findByRole("table", { name: "Due and alert dates" });
    expect(within(table).getByRole("rowheader", { name: "Oct 2026" })).toBeInTheDocument();
    expect(table).toHaveTextContent("Wed 11 Nov 2026");
    expect(table).toHaveTextContent("Thu 5 Nov 2026moved from Sat 7 Nov 2026");
    expect(screen.getByText(/Alert 4 days before the due date \(the default\)/)).toBeInTheDocument();
    expect(posts[0]?.body).toMatchObject({ frequency: "monthly", due: { months_after: 1, day: 11, overrides: {} } });
    // Nothing is saved by previewing.
    expect(posts.every((c) => c.path.startsWith("/api/admin/filings/preview"))).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Next year" }));
    expect(await screen.findByRole("rowheader", { name: "Oct 2027" })).toBeInTheDocument();
  });

  it("follows the rule as it is edited", async () => {
    const posts: Call[] = [];
    renderApp("/setup/filings/3", {
      user: admin,
      handlers: {
        ...noHealth,
        "GET /api/admin/filings/3": () => jsonResponse({ ...gstr1, versions: [calendar()] }),
        "POST /api/admin/filings/preview": (c) => { posts.push(c); return jsonResponse(preview(2026)); },
      },
    });
    await screen.findByRole("table", { name: "Due and alert dates" });
    const day = screen.getByLabelText("Due: day of that month");
    await userEvent.clear(day);
    await userEvent.type(day, "20");
    await waitFor(() => {
      expect(posts.at(-1)?.body).toMatchObject({ due: { day: 20 } });
    });
  });
});

describe("Holidays and lead times", () => {
  it("adds and removes holidays, and saves the default lead times", async () => {
    const year = new Date().getFullYear();
    const calls: Call[] = [];
    let holidays = [{ day: `${String(year)}-10-02`, name: "Gandhi Jayanti" }];
    const range = `?from_date=${String(year)}-01-01&to_date=${String(year)}-12-31`;
    renderApp("/setup/holidays", {
      user: admin,
      handlers: {
        ...noHealth,
        "GET /api/admin/lead-times": () => jsonResponse({ monthly_days: 4, quarterly_days: 10, yearly_days: 21, updated_at: "", updated_by_id: null }),
        "PUT /api/admin/lead-times": (c) => { calls.push(c); return jsonResponse({ monthly_days: 4, quarterly_days: 14, yearly_days: 21, updated_at: "", updated_by_id: 1 }); },
        [`GET /api/admin/holidays${range}`]: () => jsonResponse(holidays),
        "POST /api/admin/holidays": (c) => { calls.push(c); holidays = [...holidays, { day: `${String(year)}-11-06`, name: "Firm day" }]; return jsonResponse(holidays[1], 201); },
        [`DELETE /api/admin/holidays/${String(year)}-10-02`]: (c) => { calls.push(c); holidays = holidays.filter((h) => h.name !== "Gandhi Jayanti"); return new Response(null, { status: 204 }); },
      },
    });
    const quarterly = await screen.findByLabelText("Quarterly filings");
    await waitFor(() => { expect(quarterly).toHaveValue("10"); });
    await userEvent.clear(quarterly);
    await userEvent.type(quarterly, "14");
    await userEvent.click(screen.getByRole("button", { name: "Save lead times" }));
    expect(await screen.findByText(/Calendars without their own lead time use these/)).toBeInTheDocument();
    expect(calls[0]?.body).toEqual({ monthly_days: 4, quarterly_days: 14, yearly_days: 21 });

    expect(await screen.findByText("Gandhi Jayanti")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Date"), `${String(year)}-11-06`);
    await userEvent.type(screen.getByLabelText("Holiday"), "Firm day");
    await userEvent.click(screen.getByRole("button", { name: "Add holiday" }));
    expect(await screen.findByText("Firm day")).toBeInTheDocument();
    expect(calls[1]?.body).toEqual({ day: `${String(year)}-11-06`, name: "Firm day" });
    await userEvent.click(screen.getByRole("button", { name: "Remove Gandhi Jayanti" }));
    await waitFor(() => { expect(screen.queryByText("Gandhi Jayanti")).not.toBeInTheDocument(); });
    expect(calls[2]?.headers.get("X-CSRF-Token")).toBe("csrf-1");
  });
});
