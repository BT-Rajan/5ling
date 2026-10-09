/** The only way the UI talks to the server. Same-origin, no tokens kept in JavaScript. */

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | undefined;
  readonly body: unknown;

  constructor(status: number, code: string, message: string, requestId: string | undefined, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.body = body;
  }
}

interface ErrorEnvelope {
  error?: { code?: unknown; message?: unknown; request_id?: unknown };
}

const DEFAULT_TIMEOUT_MS = 10_000;
const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

// The CSRF token lives in memory only (never in storage), and is set after sign-in.
let csrfToken: string | null = null;
export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

// Called when the server says the session is gone, so the app can return to the sign-in page.
let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

export async function api<T>(
  path: string,
  init: Omit<RequestInit, "signal" | "credentials" | "body"> & { timeoutMs?: number; json?: unknown } = {},
): Promise<T> {
  if (!path.startsWith("/api/")) throw new Error("api() only calls /api/ paths on this site");
  const { timeoutMs = DEFAULT_TIMEOUT_MS, headers, json, ...rest } = init;
  const controller = new AbortController();
  const timer = setTimeout(() => {
    controller.abort();
  }, timeoutMs);
  try {
    const merged = new Headers(headers);
    if (!merged.has("Accept")) merged.set("Accept", "application/json");
    const method = (rest.method ?? "GET").toUpperCase();
    if (UNSAFE.has(method) && csrfToken) merged.set("X-CSRF-Token", csrfToken);
    if (json !== undefined) merged.set("Content-Type", "application/json");
    const response = await fetch(path, {
      ...rest,
      ...(json !== undefined ? { body: JSON.stringify(json) } : {}),
      headers: merged,
      credentials: "same-origin",
      signal: controller.signal,
    });
    const text = await response.text();
    let body: unknown = null;
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = null;
      }
    }
    if (response.status === 401 && path !== "/api/auth/login") onUnauthorized?.();
    if (!response.ok) {
      const err = (body as ErrorEnvelope | null)?.error;
      throw new ApiError(
        response.status,
        typeof err?.code === "string" ? err.code : `http_${response.status}`,
        typeof err?.message === "string" ? err.message : "The request failed.",
        typeof err?.request_id === "string" ? err.request_id : undefined,
        body,
      );
    }
    return body as T;
  } finally {
    clearTimeout(timer);
  }
}

/** A sentence safe to show a person for any failure. */
export function messageFor(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "We could not reach the server. Check your connection and try again.";
}
