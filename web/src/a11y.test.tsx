import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { admin, jsonResponse, renderApp, staff, stubScreen } from "./test-utils";

/**
 * BRD NFR-US-01 (WCAG 2.1 AA, keyboard use): on the main screens, every control has a name a
 * screen reader can read, and the Tab key reaches every one of them in turn, never getting stuck.
 * Colour contrast is proven separately (tokens.test.ts).
 */

const quiet = { "GET /api/health/ready": () => new Promise<Response>(() => undefined), "GET /api/admin/audit/status": () => jsonResponse({ latest: null }) };

const dash = { on: "2026-10-14", owner_id: null, stages: [{ stage: "scheduled", label: "Scheduled", count: 3 }], overdue_filings: 1, documents_missing: 0, workload: [], fees_due: { count: 0, amount: "0.00" }, fees_overdue: { count: 0, amount: "0.00" } };
const report = { report: "filing-status", title: "Filing status by period", start: "2026-08-01", end: "2026-11-14", owner_id: null, columns: ["Filing", "Period"], rows: [["GSTR-1", "Sep 2026"]], total: 1, truncated: false };

const CONTROLS = 'button:not([disabled]), a[href], input:not([type="hidden"]):not([disabled]), select, textarea, [role="combobox"]';

async function checkKeyboard() {
  const controls = Array.from(document.querySelectorAll<HTMLElement>(CONTROLS)).filter((el) => !el.closest("[aria-hidden='true']") && el.tabIndex >= 0 && !el.hidden);
  expect(controls.length).toBeGreaterThan(0);
  for (const el of controls) expect(el).toHaveAccessibleName();
  const reached = new Set<Element>();
  for (let i = 0; i < controls.length * 2 + 5; i++) {
    await userEvent.tab();
    if (document.activeElement && document.activeElement !== document.body) reached.add(document.activeElement);
  }
  const visible = controls.filter((el) => !(el instanceof HTMLInputElement && el.type === "file"));
  for (const el of visible) expect(reached.has(el) || el.closest("[inert]") !== null).toBe(true);
}

beforeEach(() => {
  stubScreen(true);
});

describe("Keyboard and screen reader basics (WCAG 2.1 AA)", () => {
  it("the sign-in screen", async () => {
    renderApp("/", { user: null, handlers: quiet });
    await screen.findAllByRole("button", { name: /Sign in|Continue/ });
    await checkKeyboard();
  });

  it("the home dashboard", async () => {
    renderApp("/", { user: admin, handlers: { ...quiet, "GET /api/dashboard": () => jsonResponse(dash), "GET /api/worklist": () => new Promise<Response>(() => undefined) } });
    await screen.findByRole("button", { name: "Overdue filings: 1" });
    await checkKeyboard();
  });

  it("the reports screen", async () => {
    renderApp("/reports", { user: staff, handlers: { ...quiet, "GET /api/reports/filing-status": () => jsonResponse(report) } });
    await screen.findByRole("table", { name: "Filing status by period" });
    await checkKeyboard();
  });
});
