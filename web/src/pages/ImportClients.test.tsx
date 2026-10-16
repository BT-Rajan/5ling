import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { admin, errorResponse, jsonResponse, renderApp, staff, stubScreen, type Call } from "../test-utils";

const quiet = { "GET /api/health/ready": () => new Promise<Response>(() => undefined), "GET /api/admin/audit/status": () => jsonResponse({ latest: null }) };

const report = {
  rows: 3,
  ready: 2,
  problems: [{ line: 4, source_id: "A3", problems: ["pan: That is not a valid PAN."] }],
  token: "1999999999.abc",
};

beforeEach(() => {
  stubScreen(true);
});

function csv(text = "kind,legal_name\nindividual,Asha") {
  return new File([text], "clients.csv", { type: "text/csv" });
}

describe("Importing clients", () => {
  it("checks first, lists the problems, and saves only after the person confirms", async () => {
    const calls: Call[] = [];
    renderApp("/clients/import", {
      user: admin,
      handlers: {
        ...quiet,
        "POST /api/clients/import/check": (c) => { calls.push(c); return jsonResponse(report); },
        "POST /api/clients/import/save?token=1999999999.abc": (c) => {
          calls.push(c);
          return jsonResponse({ saved: [{ line: 2, source_id: "A1", client_id: 100001 }, { line: 3, source_id: "A2", client_id: 100002 }], skipped: report.problems });
        },
      },
    });
    expect(await screen.findByRole("heading", { name: "Import clients" })).toBeInTheDocument();
    expect(screen.getByText(/owner_email \(who owns the client\)/)).toBeInTheDocument();
    await userEvent.upload(screen.getByLabelText("Spreadsheet file (CSV)"), csv());
    await userEvent.click(screen.getByRole("button", { name: "Check file" }));

    const checked = await screen.findByRole("region", { name: "Check result" });
    expect(within(checked).getByText(/3 rows checked: 2 ready to save, 1 with problems. Nothing has been saved yet./)).toBeInTheDocument();
    const problems = within(checked).getByRole("table", { name: "Rows with problems" });
    expect(within(problems).getByText("pan: That is not a valid PAN.")).toBeInTheDocument();
    expect(calls).toHaveLength(1); // nothing saved yet
    expect(calls[0]?.headers.get("Content-Type")).toBe("text/csv");
    expect(calls[0]?.headers.get("X-CSRF-Token")).toBe("csrf-1");

    await userEvent.click(within(checked).getByRole("button", { name: "Save 2 clients" }));
    const done = await screen.findByRole("region", { name: "Import result" });
    expect(within(done).getByText("2 clients saved; 1 row left out.")).toBeInTheDocument();
    expect(within(done).getByRole("link", { name: "100001" })).toHaveAttribute("href", "/clients/100001");
    expect(within(done).getByRole("table", { name: "Rows left out" })).toBeInTheDocument();
    expect(calls).toHaveLength(2);
  });

  it("offers nothing to save when every row has a problem, and shows a refused file's reason", async () => {
    let first = true;
    renderApp("/clients/import", {
      user: staff,
      handlers: {
        ...quiet,
        "POST /api/clients/import/check": () => {
          if (first) {
            first = false;
            return errorResponse(422, "bad_import_file", "The header row lacks: package.");
          }
          return jsonResponse({ ...report, ready: 0, rows: 1 });
        },
      },
    });
    expect(await screen.findByText(/the clients will be yours/)).toBeInTheDocument();
    await userEvent.upload(screen.getByLabelText("Spreadsheet file (CSV)"), csv());
    await userEvent.click(screen.getByRole("button", { name: "Check file" }));
    expect(await screen.findByText("The header row lacks: package.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Check file" }));
    expect(await screen.findByText("Fix the rows above in the spreadsheet, then check it again.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Save/ })).not.toBeInTheDocument();
  });

  it("refuses a file over 1 MB before sending it", async () => {
    renderApp("/clients/import", { user: staff, handlers: quiet });
    await userEvent.upload(await screen.findByLabelText("Spreadsheet file (CSV)"), csv("x".repeat(1024 * 1024 + 1)));
    expect(screen.getByText("The file is larger than 1 MB. Split it into smaller files.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check file" })).toBeDisabled();
  });

  it("is reached from the client list", async () => {
    renderApp("/clients", { user: staff, handlers: { ...quiet, "GET /api/clients?limit=25&offset=0": () => jsonResponse({ items: [], total: 0 }) } });
    expect(await screen.findByRole("link", { name: "Import" })).toHaveAttribute("href", "/clients/import");
  });
});
