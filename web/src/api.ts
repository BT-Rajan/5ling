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
let onUnauthorized: ((error: ApiError) => void) | null = null;
export function setUnauthorizedHandler(handler: ((error: ApiError) => void) | null): void {
  onUnauthorized = handler;
}

export async function api<T>(
  path: string,
  init: Omit<RequestInit, "signal" | "credentials" | "body"> & { timeoutMs?: number; json?: unknown; csv?: Blob } = {},
): Promise<T> {
  if (!path.startsWith("/api/")) throw new Error("api() only calls /api/ paths on this site");
  const { timeoutMs = DEFAULT_TIMEOUT_MS, headers, json, csv, ...rest } = init;
  const controller = new AbortController();
  const timer = setTimeout(() => {
    controller.abort();
  }, timeoutMs);
  try {
    const merged = new Headers(headers);
    if (!merged.has("Accept")) merged.set("Accept", "application/json");
    const method = (rest.method ?? "GET").toUpperCase();
    if (UNSAFE.has(method) && csrfToken && !merged.has("X-CSRF-Token")) merged.set("X-CSRF-Token", csrfToken);
    if (json !== undefined) merged.set("Content-Type", "application/json");
    if (csv !== undefined) merged.set("Content-Type", "text/csv");
    const response = await fetch(path, {
      ...rest,
      ...(json !== undefined ? { body: JSON.stringify(json) } : {}),
      ...(csv !== undefined ? { body: csv } : {}),
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
    if (!response.ok) throw failureFrom(path, response.status, body);
    return body as T;
  } finally {
    clearTimeout(timer);
  }
}

function failureFrom(path: string, status: number, body: unknown): ApiError {
  const err = (body as ErrorEnvelope | null)?.error;
  const failure = new ApiError(
    status,
    typeof err?.code === "string" ? err.code : `http_${status}`,
    typeof err?.message === "string" ? err.message : "The request failed.",
    typeof err?.request_id === "string" ? err.request_id : undefined,
    body,
  );
  // A 401 from the sign-in steps means "wrong details", not "your session ended".
  // Nor does one from the client portal, which has no staff session at all.
  if (status === 401 && !path.startsWith("/api/auth/login") && !path.startsWith("/api/auth/mfa/") && !path.startsWith("/api/portal/")) {
    onUnauthorized?.(failure);
  }
  return failure;
}

export interface Download {
  blob: Blob;
  filename: string;
  headers: Headers;
}

/** POST for a file (an export). Same rules as api(): same-origin, CSRF token, no stored tokens. */
export async function download(path: string, json: unknown, timeoutMs = 120_000): Promise<Download> {
  if (!path.startsWith("/api/")) throw new Error("download() only calls /api/ paths on this site");
  const controller = new AbortController();
  const timer = setTimeout(() => {
    controller.abort();
  }, timeoutMs);
  try {
    const headers = new Headers({ "Content-Type": "application/json" });
    if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
    const response = await fetch(path, {
      method: "POST",
      body: JSON.stringify(json),
      headers,
      credentials: "same-origin",
      signal: controller.signal,
    });
    if (!response.ok) {
      let body: unknown = null;
      try {
        body = JSON.parse(await response.text());
      } catch {
        body = null;
      }
      throw failureFrom(path, response.status, body);
    }
    const disposition = response.headers.get("Content-Disposition") ?? "";
    const named = /filename="([^"\\/]+)"/.exec(disposition)?.[1];
    return { blob: await response.blob(), filename: named ?? "download", headers: response.headers };
  } finally {
    clearTimeout(timer);
  }
}

/** Upload one file as the raw request body (no multipart), its name in X-File-Name. */
export async function uploadFile<T>(path: string, file: Blob & { name: string }, extraHeaders: Record<string, string> = {}, timeoutMs = 300_000): Promise<T> {
  if (!path.startsWith("/api/uploads/")) throw new Error("uploadFile() only posts to /api/uploads/");
  const controller = new AbortController();
  const timer = setTimeout(() => {
    controller.abort();
  }, timeoutMs);
  try {
    const headers = new Headers({ "Content-Type": "application/octet-stream", Accept: "application/json", "X-File-Name": encodeURIComponent(file.name) });
    if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
    for (const [name, value] of Object.entries(extraHeaders)) headers.set(name, value);
    const response = await fetch(path, { method: "POST", body: file, headers, credentials: "same-origin", signal: controller.signal });
    const text = await response.text();
    let body: unknown = null;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      body = null;
    }
    if (!response.ok) throw failureFrom(path, response.status, body);
    return body as T;
  } finally {
    clearTimeout(timer);
  }
}

/** Hand a downloaded file to the browser to save. */
export function saveFile({ blob, filename }: Download): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.rel = "noopener";
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 1000);
}

/** A sentence safe to show a person for any failure. */
export function messageFor(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "We could not reach the server. Check your connection and try again.";
}
