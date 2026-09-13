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
});
