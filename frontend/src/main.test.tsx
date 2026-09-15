import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./main";

const response = (body: unknown, status = 200) =>
  new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("first administrator flow", () => {
  it("shows administrator creation directly and validates the password confirmation", async () => {
    window.history.pushState({}, "", "/login");
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url =
          typeof input === "object" && input && "url" in input
            ? String(input.url)
            : String(input);
        if (url.endsWith("/api/v1/setup/status"))
          return response({
            administrator_exists: false,
            csrf_token: "initial",
          });
        if (url.endsWith("/api/v1/setup/administrator")) {
          const headers =
            typeof input === "object" && input && "headers" in input
              ? new Headers(input.headers)
              : new Headers(init?.headers);
          expect(headers.get("X-CSRF-Token")).toBe("initial");
          return response({ username: "admin", csrf_token: "created" }, 201);
        }
        if (url.endsWith("/api/v1/settings"))
          return response({
            preferences: {
              language: "en",
              timezone: "UTC",
              tile_url: "tiles",
              saved: false,
            },
            postgresql: {
              host: "",
              username: "",
              enabled: false,
              skipped: false,
              port: 5432,
              database: "teslamate",
              sslmode: "prefer",
              password_set: false,
              version: 0,
              status: "disabled",
              test_available: false,
            },
            mqtt: {
              host: "",
              username: "",
              enabled: false,
              skipped: false,
              port: 1883,
              tls: false,
              verify_tls: true,
              topic_prefix: "teslamate",
              password_set: false,
              version: 0,
              status: "disabled",
              test_available: false,
            },
            smtp: {
              host: "",
              username: "",
              enabled: false,
              skipped: false,
              port: 587,
              tls_mode: "starttls",
              verify_tls: true,
              sender: "",
              password_set: false,
              version: 0,
              status: "disabled",
              test_available: false,
            },
            onboarding: { step: "preferences", completed: false },
          });
        return response({ username: "admin" });
      });
    render(<App />);
    expect(
      await screen.findByRole("heading", { name: "Create the administrator" }),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Username/), {
      target: { value: "admin" },
    });
    fireEvent.change(screen.getByLabelText(/^Password/), {
      target: { value: "a-long-password" },
    });
    fireEvent.change(screen.getByLabelText(/Confirm password/), {
      target: { value: "a-long-password" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Create administrator" }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Create administrator" }),
      ).toBeEnabled(),
    );
    expect(fetchMock).toBeDefined();
  });

  it("submits browser-autofilled login values from the native form", async () => {
    window.history.pushState({}, "", "/login");
    const requests: Array<{ url: string; body?: string }> = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url =
        typeof input === "object" && input && "url" in input
          ? String(input.url)
          : String(input);
      const body =
        input instanceof Request
          ? await input.clone().text()
          : typeof init?.body === "string"
            ? init.body
            : undefined;
      requests.push({ url, body });
      if (url.endsWith("/api/v1/setup/status"))
        return response({ administrator_exists: true, csrf_token: "initial" });
      if (url.endsWith("/api/v1/auth/login"))
        return response({ username: "autofilled", csrf_token: "logged-in" });
      return response({ username: "autofilled" });
    });

    render(<App />);
    await screen.findByRole("heading", { name: "Sign in" });
    const username = screen.getByRole("textbox", { name: "Username" });
    const password = document.getElementById("credentials-password");
    expect(password).not.toBeNull();
    const form = username.closest("form");
    expect(form).not.toBeNull();

    // Simulate a password manager filling the DOM without dispatching React
    // change events. The submit handler must still read these values.
    Object.assign(username, { value: "autofilled" });
    Object.assign(password as HTMLInputElement, { value: "browser-password" });
    fireEvent.submit(form!);

    await waitFor(() => {
      const request = requests.find((item) => item.url.endsWith("/api/v1/auth/login"));
      expect(request).toBeDefined();
      expect(JSON.parse(String(request?.body))).toEqual({
        username: "autofilled",
        password: "browser-password",
      });
    });
  });

  it("shows and clears a mismatch from browser-autofilled creation values", async () => {
    window.history.pushState({}, "", "/login");
    const requests: Array<{ url: string; body?: string }> = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url =
        typeof input === "object" && input && "url" in input
          ? String(input.url)
          : String(input);
      const body =
        input instanceof Request
          ? await input.clone().text()
          : typeof init?.body === "string"
            ? init.body
            : undefined;
      requests.push({ url, body });
      if (url.endsWith("/api/v1/setup/status"))
        return response({ administrator_exists: false, csrf_token: "initial" });
      if (url.endsWith("/api/v1/setup/administrator"))
        return response({ username: "autofilled", csrf_token: "created" }, 201);
      return response({ username: "autofilled" });
    });

    render(<App />);
    await screen.findByRole("heading", { name: "Create the administrator" });
    const username = document.getElementById("credentials-username");
    const password = document.getElementById("credentials-password");
    const confirmation = document.getElementById(
      "credentials-password-confirmation",
    );
    const form = username?.closest("form");
    expect(form).not.toBeNull();
    Object.assign(username as HTMLInputElement, { value: "autofilled" });
    Object.assign(password as HTMLInputElement, { value: "first-password" });
    Object.assign(confirmation as HTMLInputElement, {
      value: "different-password",
    });
    fireEvent.submit(form!);

    expect(await screen.findByText("Passwords do not match.")).toBeInTheDocument();
    expect((password as HTMLInputElement).value).toBe("first-password");
    expect((confirmation as HTMLInputElement).value).toBe("different-password");
    expect(
      requests.some((item) => item.url.endsWith("/api/v1/setup/administrator")),
    ).toBe(false);

    Object.assign(confirmation as HTMLInputElement, { value: "first-password" });
    fireEvent.submit(form!);
    await waitFor(() => {
      const request = requests.find((item) =>
        item.url.endsWith("/api/v1/setup/administrator"),
      );
      expect(request).toBeDefined();
      expect(JSON.parse(String(request?.body))).toEqual({
        username: "autofilled",
        password: "first-password",
        password_confirmation: "first-password",
      });
    });
  });

  it("keeps a password login challenge outside the authenticated app and preserves leading zeroes", async () => {
    window.history.pushState({}, "", "/login");
    const requests: Array<{ url: string; body?: string }> = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = typeof input === "object" && input && "url" in input ? String(input.url) : String(input);
      const body = input instanceof Request ? await input.clone().text() : typeof init?.body === "string" ? init.body : undefined;
      requests.push({ url, body });
      if (url.endsWith("/api/v1/setup/status")) return response({ administrator_exists: true, csrf_token: "initial" });
      if (url.endsWith("/api/v1/auth/login")) return response({ status: "two_factor_required", csrf_token: "challenge", expires_in: 300 });
      if (url.endsWith("/api/v1/auth/two-factor/verify")) return response({ status: "authenticated", username: "admin", csrf_token: "session" });
      return response({ username: "admin" });
    });
    render(<App />);
    await screen.findByRole("heading", { name: "Sign in" });
    fireEvent.change(screen.getByLabelText(/^Username(?:\s*\*)?$/), { target: { value: "admin" } });
    fireEvent.change(document.getElementById("credentials-password")!, { target: { value: "password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByRole("heading", { name: "Verify your sign-in" });
    const code = screen.getByLabelText(/^Verification code(?:\s*\*)?$/) as HTMLInputElement;
    expect(code.type).toBe("text");
    expect(code.inputMode).toBe("numeric");
    fireEvent.change(code, { target: { value: "000123" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    await waitFor(() => expect(requests.find((item) => item.url.endsWith("/auth/two-factor/verify"))?.body).toBe(JSON.stringify({ method: "totp", code: "000123" })));
  });
});
