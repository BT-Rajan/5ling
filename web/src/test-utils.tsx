import { ThemeProvider } from "@mui/material/styles";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "./App";
import { AuthProvider, type User } from "./auth/AuthProvider";
import { theme } from "./theme";

export const staff: User = { id: 2, email: "asha@firm.in", full_name: "Asha Rao", role: "staff", must_change_password: false };
export const admin: User = { id: 1, email: "boss@firm.in", full_name: "The Boss", role: "administrator", must_change_password: false };

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export function errorResponse(status: number, code: string, message: string): Response {
  return jsonResponse({ error: { code, message, request_id: "req-test" } }, status);
}

export interface Call {
  method: string;
  path: string;
  body: unknown;
  headers: Headers;
}
export type Handler = (call: Call) => Response | Promise<Response>;

interface MockOptions {
  /** Who is signed in when the app starts; null means nobody (the server answers 401). */
  user?: User | null;
  /** Extra or overriding routes, keyed like "POST /api/auth/login". A handler may throw to simulate a network failure. */
  handlers?: Record<string, Handler>;
}

/** Stands in for the server. Anything not handled is a 404 and is recorded, so tests can notice it. */
export function mockApi({ user = staff, handlers = {} }: MockOptions = {}): { calls: Call[] } {
  const calls: Call[] = [];
  const defaults: Record<string, Handler> = {
    "GET /api/auth/me": () =>
      user ? jsonResponse({ user, csrf_token: "csrf-1" }) : errorResponse(401, "http_401", "Sign in to continue."),
    "GET /api/health/ready": () => jsonResponse({ status: "ok", checks: { database: "ok" } }),
  };
  const routes = { ...defaults, ...handlers };
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string, init: RequestInit = {}) => {
      const method = (init.method ?? "GET").toUpperCase();
      const call: Call = {
        method,
        path: input,
        body: typeof init.body === "string" ? (JSON.parse(init.body) as unknown) : undefined,
        headers: new Headers(init.headers),
      };
      calls.push(call);
      const handler = routes[`${method} ${input}`];
      if (!handler) return Promise.resolve(errorResponse(404, "http_404", `unmocked ${method} ${input}`));
      try {
        return Promise.resolve(handler(call));
      } catch (error) {
        return Promise.reject(error instanceof Error ? error : new Error("handler failed"));
      }
    }),
  );
  return { calls };
}

export function renderApp(path = "/", options: MockOptions = {}): ReturnType<typeof render> & { calls: Call[] } {
  const { calls } = mockApi(options);
  const utils = render(
    <ThemeProvider theme={theme}>
      <AuthProvider>
        <MemoryRouter initialEntries={[path]}>
          <App />
        </MemoryRouter>
      </AuthProvider>
    </ThemeProvider>,
  );
  return { ...utils, calls };
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
