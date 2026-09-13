import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./main";

const response = (body: unknown, status = 200) =>
  new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

afterEach(() => vi.restoreAllMocks());

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
});
