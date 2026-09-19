import { afterEach, describe, expect, it, vi } from "vitest";

const response = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const settings = {
  preferences: {
    language: "en",
    timezone: "UTC",
    tile_url: "tiles",
    saved: true,
  },
  postgresql: {},
  mqtt: {},
  smtp: {},
  onboarding: { step: "preferences", completed: false },
};

afterEach(() => vi.restoreAllMocks());

describe("typed API client", () => {
  it("refreshes an expired CSRF token once and replays a settings write", async () => {
    vi.resetModules();
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url =
        typeof input === "object" && input && "url" in input
          ? String(input.url)
          : String(input);
      calls.push(url);
      if (url.endsWith("/auth/csrf")) return response({ csrf_token: "fresh" });
      if (
        calls.filter((value) => value.endsWith("/settings/preferences"))
          .length === 1
      ) {
        return response({ detail: "CSRF validation failed" }, 403);
      }
      return response(settings);
    });
    const { setCsrf, settingsApi } = await import("./client");
    setCsrf("stale");

    await expect(
      settingsApi.preferences({
        language: "en",
        timezone: "UTC",
        tile_url: "tiles",
      }),
    ).resolves.toMatchObject({ preferences: { saved: true } });
    expect(calls.map((url) => url.split("/api/v1")[1])).toEqual([
      "/settings/preferences",
      "/auth/csrf",
      "/settings/preferences",
    ]);
  });

  it("does not replay a one-time factor proof after a CSRF rejection", async () => {
    vi.resetModules();
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "object" && input && "url" in input
        ? String(input.url) : String(input);
      calls.push(url);
      return response({ detail: "CSRF validation failed" }, 403);
    });
    const { authApi, setCsrf } = await import("./client");
    setCsrf("stale");

    await expect(authApi.verifyTwoFactor({ method: "totp", code: "000123" })).rejects.toMatchObject({ status: 403 });
    expect(calls.map((url) => url.split("/api/v1")[1])).toEqual(["/auth/two-factor/verify"]);
  });

  it("refreshes CSRF only on the next explicit factor submission after rejection", async () => {
    vi.resetModules();
    const calls: string[] = [];
    let rejected = false;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "object" && input && "url" in input ? String(input.url) : String(input);
      calls.push(url);
      if (url.endsWith("/auth/csrf")) return response({ csrf_token: "fresh" });
      if (!rejected) { rejected = true; return response({ detail: "CSRF validation failed" }, 403); }
      return response({ status: "authenticated", username: "admin", csrf_token: "session" });
    });
    const { authApi, setCsrf } = await import("./client");
    setCsrf("stale");
    await expect(authApi.verifyTwoFactor({ method: "totp", code: "000123" })).rejects.toMatchObject({ status: 403 });
    expect(calls).toHaveLength(1);
    await expect(authApi.verifyTwoFactor({ method: "totp", code: "000456" })).resolves.toMatchObject({ status: "authenticated" });
    expect(calls.map((url) => url.split("/api/v1")[1])).toEqual([
      "/auth/two-factor/verify", "/auth/csrf", "/auth/two-factor/verify",
    ]);
  });

  it("retains a structured history-source error code", async () => {
    vi.resetModules();
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      response({ detail: { code: "insufficient_permissions" } }, 503),
    );
    const { ApiError, historyApi } = await import("./client");

    await expect(historyApi.capabilities()).rejects.toEqual(
      expect.objectContaining({
        status: 503,
        sourceCode: "insufficient_permissions",
      }),
    );
    await historyApi.capabilities().catch((error: unknown) => {
      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({ message: "Request failed" });
    });
  });

  it("passes cancellation through the charge-series request", async () => {
    vi.resetModules();
    let requestSignal: AbortSignal | null = null;
    let requestUrl = "";
    let receivedAbort = false;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      requestUrl = input instanceof Request ? input.url : String(input);
      requestSignal = init?.signal ?? (input instanceof Request ? input.signal : null);
      requestSignal?.addEventListener("abort", () => { receivedAbort = true; });
      return response({ items: [] });
    });
    const { historyApi } = await import("./client");
    const controller = new AbortController();

    await historyApi.chargeSeries(7, { signal: controller.signal });
    expect(requestSignal).not.toBeNull();
    expect(requestUrl).toContain("/api/v1/charges/7/series");
    controller.abort();
    expect(receivedAbort).toBe(true);
  });

});
