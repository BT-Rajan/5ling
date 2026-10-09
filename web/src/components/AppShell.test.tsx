import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { admin, renderApp, staff, stubScreen } from "../test-utils";

const noHealth = { "GET /api/health/ready": () => new Promise<Response>(() => undefined) };

async function mainNav() {
  return within(await screen.findByRole("navigation", { name: "Main" }));
}

describe("App shell", () => {
  it("phone: top bar and a bottom navigation", async () => {
    stubScreen(false);
    renderApp("/", { handlers: noHealth });
    const nav = await mainNav();
    expect(nav.getAllByRole("link").map((a) => a.textContent)).toEqual(["Home", "Work list", "Clients"]);
    expect(screen.getByRole("banner")).toBeInTheDocument();
  });

  it("desktop: a navigation rail and no top bar", async () => {
    stubScreen(true);
    renderApp("/", { handlers: noHealth });
    await mainNav();
    expect(screen.getAllByRole("navigation", { name: "Main" })).toHaveLength(1);
    expect(screen.queryByRole("banner")).not.toBeInTheDocument();
  });

  it("the menu shows Team to an Administrator and not to Staff", async () => {
    stubScreen(false);
    const first = renderApp("/", { user: staff, handlers: noHealth });
    expect((await mainNav()).queryByRole("link", { name: "Team" })).not.toBeInTheDocument();
    first.unmount();
    renderApp("/", { user: admin, handlers: noHealth });
    expect((await mainNav()).getByRole("link", { name: "Team" })).toBeInTheDocument();
  });

  it("marks the current destination", async () => {
    stubScreen(false);
    renderApp("/", { handlers: noHealth });
    const nav = await mainNav();
    expect(nav.getByRole("link", { name: "Home" })).toHaveAttribute("aria-current", "page");
    await userEvent.click(nav.getByRole("link", { name: "Clients" }));
    expect(nav.getByRole("link", { name: "Clients" })).toHaveAttribute("aria-current", "page");
    expect(nav.getByRole("link", { name: "Home" })).not.toHaveAttribute("aria-current");
    expect(screen.getByText(/No clients yet/)).toBeInTheDocument();
  });

  it("updates the page title and moves focus to the page after navigating", async () => {
    stubScreen(false);
    renderApp("/", { handlers: noHealth });
    const nav = await mainNav();
    await userEvent.click(nav.getByRole("link", { name: "Work list" }));
    expect(document.title).toBe("Work list · Ledgerline");
    expect(screen.getByRole("main")).toHaveFocus();
  });

  it("has a skip link to the main content", async () => {
    stubScreen(false);
    renderApp("/", { handlers: noHealth });
    expect(await screen.findByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#main");
  });

  it("shows a clear message for unknown addresses", async () => {
    stubScreen(false);
    renderApp("/nowhere", { handlers: noHealth });
    expect(await screen.findByText(/Check the address/)).toBeInTheDocument();
  });
});
