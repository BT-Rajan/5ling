import { api, ApiError, setCsrfToken, setUnauthorizedHandler } from "./api";
import { jsonResponse } from "./test-utils";

describe("api()", () => {
  it("refuses anything that is not an /api/ path on this site", async () => {
    const spy = vi.fn();
    vi.stubGlobal("fetch", spy);
    await expect(api("https://evil.example/steal")).rejects.toThrow();
    await expect(api("//evil.example/api/x")).rejects.toThrow();
    expect(spy).not.toHaveBeenCalled();
  });

  it("sends same-origin credentials and no secrets of its own", async () => {
    const spy = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", spy);
    await api("/api/health/live");
    const init = spy.mock.calls[0]?.[1] as RequestInit;
    expect(init.credentials).toBe("same-origin");
    expect((init.headers as Headers).get("Accept")).toBe("application/json");
    expect([...(init.headers as Headers).keys()]).toEqual(["accept"]);
  });

  it("turns the server's error envelope into an ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ error: { code: "validation_error", message: "Some fields are not valid.", request_id: "r-1" } }, 422),
      ),
    );
    await expect(api("/api/x")).rejects.toMatchObject({
      status: 422,
      code: "validation_error",
      requestId: "r-1",
    });
  });

  it("survives a non-JSON error page", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>bad gateway</html>", { status: 502 })));
    const error = await api("/api/x").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 502, code: "http_502" });
  });

  it("gives up after the timeout", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: string, init: RequestInit) => {
        return new Promise((_resolve, reject) => {
          init.signal?.addEventListener("abort", () => {
            reject(new DOMException("aborted", "AbortError"));
          });
        });
      }),
    );
    await expect(api("/api/slow", { timeoutMs: 20 })).rejects.toMatchObject({ name: "AbortError" });
  });

  it("adds the CSRF token to state-changing requests only, and only once it is known", async () => {
    const spy = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse({})));
    vi.stubGlobal("fetch", spy);
    await api("/api/x", { method: "POST" });
    setCsrfToken("tok-123");
    await api("/api/x");
    await api("/api/x", { method: "POST", json: { a: 1 } });
    await api("/api/x", { method: "DELETE" });
    const sent = spy.mock.calls.map((c) => (c[1] as RequestInit).headers as Headers);
    expect(sent[0]?.has("X-CSRF-Token")).toBe(false);
    expect(sent[1]?.has("X-CSRF-Token")).toBe(false);
    expect(sent[2]?.get("X-CSRF-Token")).toBe("tok-123");
    expect(sent[2]?.get("Content-Type")).toBe("application/json");
    expect(sent[3]?.get("X-CSRF-Token")).toBe("tok-123");
    expect((spy.mock.calls[2]?.[1] as RequestInit).body).toBe('{"a":1}');
  });

  it("tells the app when the session has ended, except for a failed sign-in", async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(jsonResponse({ error: { code: "http_401", message: "no" } }, 401))));
    await api("/api/anything").catch(() => undefined);
    expect(handler).toHaveBeenCalledTimes(1);
    await api("/api/auth/login", { method: "POST" }).catch(() => undefined);
    expect(handler).toHaveBeenCalledTimes(1);
    setUnauthorizedHandler(null);
  });
});
