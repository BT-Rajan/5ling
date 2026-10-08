import type { ReactElement } from "react";
import { ThemeProvider } from "@mui/material/styles";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "./App";
import { theme } from "./theme";

export function renderApp(path = "/"): ReturnType<typeof render> {
  return renderWith(<App />, path);
}

export function renderWith(ui: ReactElement, path = "/"): ReturnType<typeof render> {
  return render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
    </ThemeProvider>,
  );
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** Pretend the screen is wide (desktop) or narrow (phone). */
export function stubScreen(wide: boolean): void {
  vi.stubGlobal(
    "matchMedia",
    (query: string): MediaQueryList =>
      ({
        matches: wide && query.includes("min-width"),
        media: query,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        addListener: () => undefined,
        removeListener: () => undefined,
        dispatchEvent: () => false,
        onchange: null,
      }) as MediaQueryList,
  );
}
