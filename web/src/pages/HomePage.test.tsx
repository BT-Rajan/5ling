import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { jsonResponse, renderApp, stubScreen } from "../test-utils";

beforeEach(() => {
  stubScreen(true);
});

describe("Home status", () => {
  it("shows running and connected when the server is ready", async () => {
    renderApp("/");
    expect(await screen.findByText("Running")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();
  });

  it("says the database needs setup when migrations have not run", async () => {
    renderApp("/", {
      handlers: {
        "GET /api/health/ready": () => jsonResponse({ status: "unavailable", checks: { database: "not_migrated" } }, 503),
      },
    });
    expect(await screen.findByText("Needs setup")).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
  });

  it("says the service is not reachable when the network fails", async () => {
    renderApp("/", {
      handlers: {
        "GET /api/health/ready": () => {
          throw new TypeError("network down");
        },
      },
    });
    expect(await screen.findByText("Not reachable")).toBeInTheDocument();
    expect(screen.getByText("Not checked")).toBeInTheDocument();
  });

  it("checks again when asked", async () => {
    let attempts = 0;
    renderApp("/", {
      handlers: {
        "GET /api/health/ready": () => {
          attempts += 1;
          if (attempts === 1) throw new TypeError("down");
          return jsonResponse({ status: "ok", checks: { database: "ok" } });
        },
      },
    });
    await screen.findByText("Not reachable");
    await userEvent.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() => {
      expect(screen.getByText("Running")).toBeInTheDocument();
    });
    expect(attempts).toBe(2);
  });
});
