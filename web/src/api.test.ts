import { api, ApiError } from "./api";
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
});
