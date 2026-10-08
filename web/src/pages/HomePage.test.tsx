import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { jsonResponse, renderApp, stubScreen } from "../test-utils";

beforeEach(() => {
  stubScreen(true);
});

describe("Home status", () => {
  it("shows running and connected when the server is ready", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ status: "ok", checks: { database: "ok" } })));
    renderApp("/");
    expect(await screen.findByText("Running")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();
  });

  it("says the database needs setup when migrations have not run", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ status: "unavailable", checks: { database: "not_migrated" } }, 503)),
    );
    renderApp("/");
    expect(await screen.findByText("Needs setup")).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
  });

  it("says the service is not reachable when the network fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network down")));
    renderApp("/");
    expect(await screen.findByText("Not reachable")).toBeInTheDocument();
    expect(screen.getByText("Not checked")).toBeInTheDocument();
  });

  it("checks again when asked", async () => {
    const spy = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("down"))
      .mockResolvedValue(jsonResponse({ status: "ok", checks: { database: "ok" } }));
    vi.stubGlobal("fetch", spy);
    renderApp("/");
    await screen.findByText("Not reachable");
    await userEvent.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() => {
      expect(screen.getByText("Running")).toBeInTheDocument();
    });
    expect(spy).toHaveBeenCalledTimes(2);
  });
});
