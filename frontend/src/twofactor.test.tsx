import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { focusManager, QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./main";
import { ApiError, authApi, settingsApi } from "./api/client";

vi.mock("qrcode", () => ({ toDataURL: vi.fn(async () => "data:image/png;base64,local") }));
const secret = "ABCDEFGHIJKLMNOPABCDEFGHIJKLMNOP";
const enrollment = { secret, provisioning_uri: `otpauth://totp/MateScope?secret=${secret}`, expires_in: 600 };
const recovered = { status: "authenticated" as const, username: "admin", csrf_token: "rotated", recovery_codes: ["recovery-secret"] };
const currentPassword = "synthetic-current-password";
const clients: QueryClient[] = [];
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
async function setup(enabled = false, path = "/settings", remaining = 10) {
  window.history.pushState({}, "", path);
  vi.spyOn(authApi, "setupStatus").mockResolvedValue({ administrator_exists: true, csrf_token: "initial" });
  vi.spyOn(authApi, "me").mockResolvedValue({ username: "admin" });
  vi.spyOn(authApi, "twoFactorStatus").mockResolvedValue({ enabled, recovery_codes_remaining: remaining });
  vi.spyOn(authApi, "cancelTwoFactor").mockResolvedValue(undefined);
  const connection = { host: "", username: "", enabled: false, skipped: false, password_set: false, version: 0, status: "disabled", test_available: false };
  vi.spyOn(settingsApi, "get").mockResolvedValue({
    preferences: { language: "en", timezone: "UTC", tile_url: "", saved: true },
    postgresql: { ...connection, port: 5432, database: "teslamate", sslmode: "prefer" },
    mqtt: { ...connection, port: 1883, tls: false, verify_tls: true, topic_prefix: "teslamate" },
    smtp: { ...connection, port: 587, tls_mode: "starttls", verify_tls: true, sender: "" },
    onboarding: { step: path === "/setup" ? "two_factor" : "review", completed: true },
  } as Awaited<ReturnType<typeof settingsApi.get>>);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  clients.push(client);
  const view = render(<App client={client} />);
  await screen.findByRole("heading", { name: path === "/login" ? "Sign in" : path === "/setup" ? "Setup" : "Settings" });
  return { client, ...view };
}
function fill(id: string, value: string) {
  const input = document.getElementById(id)!;
  Object.assign(input, { value }); // Password manager / native DOM values.
  return input;
}
function submit(id: string) { fireEvent.submit(document.getElementById(id)!.closest("form")!); }
async function beginEnrollment() {
  fireEvent.click(await screen.findByRole("button", { name: "Enable two-factor authentication" }));
  fill("two-factor-enroll-password", currentPassword);
  submit("two-factor-enroll-password");
}
afterEach(() => { cleanup(); clients.splice(0).forEach((client) => client.clear()); vi.restoreAllMocks(); });

describe("two-factor operation lifetimes", () => {
  it("ignores a login verification that arrives after cancellation and clears native secrets", async () => {
    const late = deferred<Awaited<ReturnType<typeof authApi.verifyTwoFactor>>>();
    vi.spyOn(authApi, "login").mockResolvedValue({ status: "two_factor_required", csrf_token: "challenge", expires_in: 300 });
    vi.spyOn(authApi, "verifyTwoFactor").mockReturnValue(late.promise);
    const { client } = await setup(false, "/login");
    fill("credentials-username", "admin"); fill("credentials-password", currentPassword); submit("credentials-password");
    await screen.findByRole("heading", { name: "Verify your sign-in" });
    fill("login-two-factor-code", "000123"); submit("login-two-factor-code");
    expect(authApi.verifyTwoFactor).toHaveBeenCalledWith({ method: "totp", code: "000123" });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await act(async () => { late.resolve(recovered); });
    expect(window.location.pathname).toBe("/login");
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeVisible();
    expect(document.getElementById("credentials-password")).toHaveValue("");
    expect(authApi.cancelTwoFactor).toHaveBeenCalledOnce();
    expect(client.getMutationCache().getAll()).toHaveLength(0);
  });

  it("drops enrollment responses after cancel, after unmount, and when another enrollment has started", async () => {
    const first = deferred<typeof enrollment>();
    const second = deferred<typeof enrollment>();
    vi.spyOn(authApi, "enrollTwoFactor").mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const view = await setup();
    await beginEnrollment();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await beginEnrollment();
    await act(async () => { first.resolve(enrollment); });
    expect(screen.queryByRole("heading", { name: "Scan with your authenticator app" })).toBeNull();
    view.unmount();
    await act(async () => { second.resolve(enrollment); });
    expect(JSON.stringify(view.client.getQueryCache().getAll().map((query) => query.state.data))).not.toContain(secret);
    expect(view.client.getMutationCache().getAll()).toHaveLength(0);
  });

  it("never restores recovery codes from a cancelled confirmation", async () => {
    vi.spyOn(authApi, "enrollTwoFactor").mockResolvedValue(enrollment);
    const late = deferred<typeof recovered>();
    vi.spyOn(authApi, "confirmTwoFactor").mockReturnValue(late.promise);
    const { client } = await setup();
    await beginEnrollment();
    await screen.findByRole("heading", { name: "Scan with your authenticator app" });
    fill("two-factor-confirm-password", currentPassword); fill("two-factor-confirm-code", "000123"); submit("two-factor-confirm-code");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await act(async () => { late.resolve(recovered); });
    expect(screen.queryByText(secret)).toBeNull();
    expect(screen.queryByText("recovery-secret")).toBeNull();
    expect(client.getMutationCache().getAll()).toHaveLength(0);
  });

  it.each(["cancel", "unmount"])("discards late recovery regeneration on %s", async (exit) => {
    const late = deferred<typeof recovered>();
    vi.spyOn(authApi, "regenerateRecoveryCodes").mockReturnValue(late.promise);
    const view = await setup(true);
    fireEvent.click(await screen.findByRole("button", { name: "Generate new recovery codes" }));
    fill("two-factor-management-password", currentPassword); fill("management-code", "000123"); submit("management-code");
    if (exit === "cancel") fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    else view.unmount();
    await act(async () => { late.resolve(recovered); });
    expect(screen.queryByText("recovery-secret")).toBeNull();
    expect(view.client.getMutationCache().getAll()).toHaveLength(0);
  });

  it("keeps one-time codes out of caches and releases them after acknowledgement", async () => {
    vi.spyOn(authApi, "enrollTwoFactor").mockResolvedValue(enrollment);
    vi.spyOn(authApi, "confirmTwoFactor").mockResolvedValue(recovered);
    const { client } = await setup();
    await beginEnrollment();
    await screen.findByRole("heading", { name: "Scan with your authenticator app" });
    fill("two-factor-confirm-password", currentPassword); fill("two-factor-confirm-code", "000123"); submit("two-factor-confirm-code");
    await screen.findByText("recovery-secret");
    const cache = JSON.stringify(client.getQueryCache().getAll().map((query) => query.state.data));
    for (const value of [secret, enrollment.provisioning_uri, currentPassword, "000123", "recovery-secret"]) expect(cache).not.toContain(value);
    expect(client.getMutationCache().getAll()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "I saved these codes" }));
    expect(screen.queryByText("recovery-secret")).toBeNull();
  });

  it.each(["/settings", "/setup"])("keeps enrollment recovery codes visible through a focus status refresh at %s", async (path) => {
    vi.spyOn(authApi, "enrollTwoFactor").mockResolvedValue(enrollment);
    vi.spyOn(authApi, "confirmTwoFactor").mockResolvedValue(recovered);
    const { client } = await setup(false, path);
    await beginEnrollment();
    await screen.findByRole("heading", { name: "Scan with your authenticator app" });
    fill("two-factor-confirm-password", currentPassword); fill("two-factor-confirm-code", "000123"); submit("two-factor-confirm-code");
    await screen.findByText("recovery-secret");

    vi.mocked(authApi.twoFactorStatus).mockResolvedValue({ enabled: true, recovery_codes_remaining: 10 });
    focusManager.setFocused(false); focusManager.setFocused(true);
    await waitFor(() => expect(authApi.twoFactorStatus).toHaveBeenCalledTimes(2));
    expect(screen.getByText("recovery-secret")).toBeVisible();

    vi.mocked(authApi.twoFactorStatus).mockRejectedValue(new ApiError(503, "status unavailable"));
    focusManager.setFocused(false); focusManager.setFocused(true);
    await waitFor(() => expect(authApi.twoFactorStatus).toHaveBeenCalledTimes(3));
    expect(screen.getByText("recovery-secret")).toBeVisible();

    vi.mocked(authApi.twoFactorStatus).mockResolvedValue({ enabled: true, recovery_codes_remaining: 10 });
    fireEvent.click(screen.getByRole("button", { name: "I saved these codes" }));
    await waitFor(() => expect(screen.queryByText("recovery-secret")).toBeNull());
    await screen.findByText("10 recovery codes remaining");
    await waitFor(() => expect(client.getQueryData(["two-factor"])).toMatchObject({ enabled: true, recovery_codes_remaining: 10 }));
  });

  it.each(["/settings", "/setup"])("refreshes enabled state after saving codes without a focus event at %s", async (path) => {
    vi.spyOn(authApi, "enrollTwoFactor").mockResolvedValue(enrollment);
    vi.spyOn(authApi, "confirmTwoFactor").mockResolvedValue(recovered);
    await setup(false, path);
    await beginEnrollment();
    await screen.findByRole("heading", { name: "Scan with your authenticator app" });
    vi.mocked(authApi.twoFactorStatus).mockResolvedValue({ enabled: true, recovery_codes_remaining: 10 });
    fill("two-factor-confirm-password", currentPassword); fill("two-factor-confirm-code", "000123"); submit("two-factor-confirm-code");
    await screen.findByText("recovery-secret");
    fireEvent.click(screen.getByRole("button", { name: "I saved these codes" }));
    await screen.findByText("10 recovery codes remaining");
    expect(screen.queryByText("recovery-secret")).toBeNull();
    expect(screen.queryByRole("button", { name: "Enable two-factor authentication" })).toBeNull();
    if (path === "/settings") {
      expect(screen.getByRole("button", { name: "Disable two-factor authentication" })).toBeVisible();
      expect(screen.getByRole("button", { name: "Generate new recovery codes" })).toBeVisible();
      expect(document.getElementById("password-proof-code")).toBeVisible();
    } else expect(screen.getByRole("button", { name: "Continue" })).toBeVisible();
  });

  it.each([
    ["/settings", "enabled"], ["/setup", "enabled"],
    ["/settings", "error"], ["/setup", "error"],
  ])("preserves pending confirmation at %s when a focus refresh returns %s first", async (path, result) => {
    vi.spyOn(authApi, "enrollTwoFactor").mockResolvedValue(enrollment);
    const late = deferred<typeof recovered>();
    vi.spyOn(authApi, "confirmTwoFactor").mockReturnValue(late.promise);
    const { client } = await setup(false, path);
    await beginEnrollment();
    await screen.findByRole("heading", { name: "Scan with your authenticator app" });
    fill("two-factor-confirm-password", currentPassword); fill("two-factor-confirm-code", "000123"); submit("two-factor-confirm-code");
    if (result === "enabled") vi.mocked(authApi.twoFactorStatus).mockResolvedValue({ enabled: true, recovery_codes_remaining: 10 });
    else vi.mocked(authApi.twoFactorStatus).mockRejectedValue(new ApiError(503, "status unavailable"));
    focusManager.setFocused(false); focusManager.setFocused(true);
    await waitFor(() => expect(authApi.twoFactorStatus).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(client.getQueryState(["two-factor"])?.fetchStatus).toBe("idle"));
    expect(screen.getByRole("heading", { name: "Scan with your authenticator app" })).toBeVisible();
    expect(screen.queryByText("recovery-secret")).toBeNull();
    await act(async () => { late.resolve(recovered); });
    await screen.findByText("recovery-secret");
    const cache = JSON.stringify(client.getQueryCache().getAll().map((query) => query.state.data));
    for (const value of [secret, enrollment.provisioning_uri, currentPassword, "000123", "recovery-secret"]) expect(cache).not.toContain(value);
    expect(client.getMutationCache().getAll()).toHaveLength(0);
    vi.mocked(authApi.twoFactorStatus).mockResolvedValue({ enabled: true, recovery_codes_remaining: 10 });
    fireEvent.click(screen.getByRole("button", { name: "I saved these codes" }));
    await screen.findByText("10 recovery codes remaining");
    expect(screen.queryByText("recovery-secret")).toBeNull();
  });

  it.each([
    ["/settings", "cancel"], ["/setup", "cancel"],
    ["/settings", "unmount"], ["/setup", "unmount"],
  ])("still discards pending confirmation at %s on explicit %s after a status refresh", async (path, exit) => {
    vi.spyOn(authApi, "enrollTwoFactor").mockResolvedValue(enrollment);
    const late = deferred<typeof recovered>();
    vi.spyOn(authApi, "confirmTwoFactor").mockReturnValue(late.promise);
    const view = await setup(false, path);
    await beginEnrollment();
    await screen.findByRole("heading", { name: "Scan with your authenticator app" });
    fill("two-factor-confirm-password", currentPassword); fill("two-factor-confirm-code", "000123"); submit("two-factor-confirm-code");
    vi.mocked(authApi.twoFactorStatus).mockResolvedValue({ enabled: true, recovery_codes_remaining: 10 });
    focusManager.setFocused(false); focusManager.setFocused(true);
    await waitFor(() => expect(authApi.twoFactorStatus).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(view.client.getQueryState(["two-factor"])?.fetchStatus).toBe("idle"));
    if (exit === "cancel") fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    else view.unmount();
    await act(async () => { late.resolve(recovered); });
    expect(screen.queryByText("recovery-secret")).toBeNull();
    expect(screen.queryByText(secret)).toBeNull();
    const cache = JSON.stringify(view.client.getQueryCache().getAll().map((query) => query.state.data));
    expect(cache).not.toContain("recovery-secret");
    expect(view.client.getMutationCache().getAll()).toHaveLength(0);
  });

  it("clears expired enrollment secrets and reports the factor error", async () => {
    vi.spyOn(authApi, "enrollTwoFactor").mockResolvedValue(enrollment);
    vi.spyOn(authApi, "confirmTwoFactor").mockRejectedValue(new ApiError(409, "Two-factor enrollment expired or invalid"));
    await setup(); await beginEnrollment();
    await screen.findByRole("heading", { name: "Scan with your authenticator app" });
    fill("two-factor-confirm-password", currentPassword); fill("two-factor-confirm-code", "000123"); submit("two-factor-confirm-code");
    await screen.findByText("This setup expired or is no longer valid. Start again.");
    expect(screen.queryByText(secret)).toBeNull();
    expect(screen.queryByRole("img", { name: "Authenticator setup QR code" })).toBeNull();
  });

  it("warns when recovery codes are exhausted", async () => {
    await setup(true, "/settings", 0);
    await screen.findByText("No recovery codes remain. Generate a new set before you need one.");
  });

  it.each([
    [401, "Two-factor challenge expired or invalid", "This sign-in verification expired or was cancelled. Sign in with your password again."],
    [429, "Too many verification attempts; retry later", "Too many verification attempts. Wait before trying again."],
    [503, "Two-factor configuration is invalid; use local recovery", "Two-factor authentication is unavailable. Use local recovery."],
  ])("distinguishes challenge errors (%s)", async (status, detail, message) => {
    vi.spyOn(authApi, "login").mockResolvedValue({ status: "two_factor_required", csrf_token: "challenge", expires_in: 300 });
    vi.spyOn(authApi, "verifyTwoFactor").mockRejectedValue(new ApiError(Number(status), String(detail)));
    await setup(false, "/login");
    fill("credentials-username", "admin"); fill("credentials-password", currentPassword); submit("credentials-password");
    await screen.findByRole("heading", { name: "Verify your sign-in" });
    fill("login-two-factor-code", "000123"); submit("login-two-factor-code");
    await screen.findByText(String(message));
    await waitFor(() => expect(screen.getByRole("button", { name: "Verify" })).toBeEnabled());
  });
});

describe("two-factor outer query lifetimes", () => {
  it.each([
    ["/settings", "me", "shown"], ["/setup", "me", "shown"],
    ["/settings", "settings", "shown"], ["/setup", "settings", "shown"],
    ["/settings", "me", "pending"], ["/setup", "me", "pending"],
    ["/settings", "settings", "pending"], ["/setup", "settings", "pending"],
    ["/settings", "me", "regeneration"], ["/settings", "settings", "regeneration"],
  ])("keeps one-time response at %s through %s 503 while %s", async (path, query, stage) => {
    const late = deferred<typeof recovered>();
    vi.spyOn(authApi, "enrollTwoFactor").mockResolvedValue(enrollment);
    vi.spyOn(authApi, "confirmTwoFactor").mockReturnValue(stage === "pending" ? late.promise : Promise.resolve(recovered));
    vi.spyOn(authApi, "regenerateRecoveryCodes").mockResolvedValue(recovered);
    const { client } = await setup(stage === "regeneration", path);
    if (stage === "regeneration") {
      fireEvent.click(await screen.findByRole("button", { name: "Generate new recovery codes" }));
      fill("two-factor-management-password", currentPassword); fill("management-code", "000123"); submit("management-code");
    } else {
      await beginEnrollment();
      await screen.findByRole("heading", { name: "Scan with your authenticator app" });
      fill("two-factor-confirm-password", currentPassword); fill("two-factor-confirm-code", "000123"); submit("two-factor-confirm-code");
    }
    if (stage !== "pending") await screen.findByText("recovery-secret");
    await waitFor(() => expect(client.isFetching()).toBe(0));
    const reader = vi.mocked(query === "me" ? authApi.me : settingsApi.get);
    const readMe = vi.mocked(authApi.me).getMockImplementation()!;
    const readSettings = vi.mocked(settingsApi.get).getMockImplementation()!;
    const calls = reader.mock.calls.length;
    reader.mockRejectedValue(new ApiError(503, "synthetic temporary unavailable"));
    focusManager.setFocused(false); focusManager.setFocused(true);
    await waitFor(() => expect(reader.mock.calls.length).toBeGreaterThan(calls));
    await waitFor(() => expect(client.getQueryState([query])?.error).toMatchObject({ status: 503 }));
    await screen.findByText("We could not load this page. Please try again.");
    if (stage === "pending") {
      expect(screen.getByRole("heading", { name: "Scan with your authenticator app" })).toBeVisible();
      await act(async () => { late.resolve(recovered); });
    }
    await screen.findByText("recovery-secret");
    await waitFor(() => expect(client.isFetching()).toBe(0));
    vi.mocked(authApi.me).mockImplementation(readMe);
    vi.mocked(settingsApi.get).mockImplementation(readSettings);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(client.getQueryState([query])?.status).toBe("success"));
    await waitFor(() => expect(client.isFetching()).toBe(0));
    await screen.findByText("recovery-secret");
    expect(screen.queryByText("We could not load this page. Please try again.")).toBeNull();
    const cache = JSON.stringify(client.getQueryCache().getAll().map((q) => q.state.data));
    for (const value of [secret, enrollment.provisioning_uri, currentPassword, "000123", "recovery-secret"]) expect(cache).not.toContain(value);
    expect(client.getMutationCache().getAll()).toHaveLength(0);
    vi.mocked(authApi.twoFactorStatus).mockResolvedValue({ enabled: true, recovery_codes_remaining: 10 });
    fireEvent.click(screen.getByRole("button", { name: "I saved these codes" }));
    expect(screen.queryByText("recovery-secret")).toBeNull();
    await screen.findByText("10 recovery codes remaining");
  });

  it.each([
    ["/settings", "me", "shown"], ["/setup", "me", "shown"],
    ["/settings", "settings", "shown"], ["/setup", "settings", "shown"],
    ["/settings", "me", "pending"], ["/setup", "me", "pending"],
    ["/settings", "settings", "pending"], ["/setup", "settings", "pending"],
  ])("clears secrets at %s when %s confirms 401 while %s", async (path, query, stage) => {
    const late = deferred<typeof recovered>();
    vi.spyOn(authApi, "enrollTwoFactor").mockResolvedValue(enrollment);
    vi.spyOn(authApi, "confirmTwoFactor").mockReturnValue(stage === "pending" ? late.promise : Promise.resolve(recovered));
    const { client } = await setup(false, path);
    await beginEnrollment();
    await screen.findByRole("heading", { name: "Scan with your authenticator app" });
    fill("two-factor-confirm-password", currentPassword); fill("two-factor-confirm-code", "000123"); submit("two-factor-confirm-code");
    if (stage === "shown") await screen.findByText("recovery-secret");
    await waitFor(() => expect(client.isFetching()).toBe(0));
    sessionStorage.setItem("matescope-history-navigation", JSON.stringify({
      cursors: { "/trips?vehicle=1": [null, "opaque"] },
      returns: { "/trips?vehicle=1": { recordId: 42, scrollY: 640 } },
    }));
    vi.mocked(query === "me" ? authApi.me : settingsApi.get).mockRejectedValue(new ApiError(401, "Not authenticated"));
    focusManager.setFocused(false); focusManager.setFocused(true);
    await screen.findByRole("heading", { name: "Sign in" });
    await act(async () => { late.resolve(recovered); });
    expect(window.location.pathname).toBe("/login");
    expect(screen.queryByText(secret)).toBeNull();
    expect(screen.queryByText("recovery-secret")).toBeNull();
    expect(JSON.stringify(client.getQueryCache().getAll().map((q) => q.state.data))).not.toContain("recovery-secret");
    expect(client.getMutationCache().getAll()).toHaveLength(0);
    expect(sessionStorage.getItem("matescope-history-navigation")).toBeNull();
  });
});
