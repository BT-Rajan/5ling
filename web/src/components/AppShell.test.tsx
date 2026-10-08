import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp, stubScreen } from "../test-utils";

beforeEach(() => {
  // The shell tests do not care about health: leave the request pending so nothing updates late.
  vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise<Response>(() => undefined)));
});

describe("App shell", () => {
  it("phone: top bar and a bottom navigation with three destinations", () => {
    stubScreen(false);
    renderApp("/");
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getAllByRole("link").map((a) => a.textContent)).toEqual(["Home", "Work list", "Clients"]);
    expect(screen.getByRole("banner")).toBeInTheDocument();
  });

  it("desktop: a navigation rail and no top bar", () => {
    stubScreen(true);
    renderApp("/");
    expect(screen.getAllByRole("navigation", { name: "Main" })).toHaveLength(1);
    expect(screen.queryByRole("banner")).not.toBeInTheDocument();
  });

  it("marks the current destination", async () => {
    stubScreen(false);
    renderApp("/");
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Home" })).toHaveAttribute("aria-current", "page");
    await userEvent.click(within(nav).getByRole("link", { name: "Clients" }));
    expect(within(nav).getByRole("link", { name: "Clients" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Home" })).not.toHaveAttribute("aria-current");
    expect(screen.getByText(/No clients yet/)).toBeInTheDocument();
  });

  it("updates the page title and moves focus to the page after navigating", async () => {
    stubScreen(false);
    renderApp("/");
    const nav = screen.getByRole("navigation", { name: "Main" });
    await userEvent.click(within(nav).getByRole("link", { name: "Work list" }));
    expect(document.title).toBe("Work list · Ledgerline");
    expect(screen.getByRole("main")).toHaveFocus();
  });

  it("has a skip link to the main content", () => {
    stubScreen(false);
    renderApp("/");
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#main");
  });

  it("shows a clear message for unknown addresses", () => {
    stubScreen(false);
    renderApp("/nowhere");
    expect(screen.getByText(/Check the address/)).toBeInTheDocument();
  });
});
