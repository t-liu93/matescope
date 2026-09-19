import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

let csrfToken = "";

export class ApiError extends Error {
  public readonly sourceCode?: SourceErrorCode;

  constructor(
    public readonly status: number,
    message: string,
    sourceCode?: SourceErrorCode,
  ) {
    super(message);
    this.sourceCode = sourceCode;
  }
}

export const sourceErrorCodes = [
  "unconfigured",
  "disabled",
  "skipped",
  "invalid_credentials",
  "unavailable",
  "timeout",
  "incompatible_schema",
  "unsafe_permissions",
  "insufficient_permissions",
] as const;

export type SourceErrorCode = (typeof sourceErrorCodes)[number];

function sourceErrorCode(error: unknown): SourceErrorCode | undefined {
  if (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    typeof error.code === "string" &&
    (sourceErrorCodes as readonly string[]).includes(error.code)
  ) {
    return error.code as SourceErrorCode;
  }
  return undefined;
}

function isWrite(method: string) {
  return !["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase());
}

function isRequestLike(input: RequestInfo | URL): input is Request {
  return (
    typeof input === "object" &&
    input !== null &&
    "method" in input &&
    "headers" in input
  );
}

async function requestWithCsrf(
  input: RequestInfo | URL,
  init?: RequestInit,
  retryCsrf = true,
): Promise<Response> {
  const method = init?.method ?? (isRequestLike(input) ? input.method : "GET");
  const write = isWrite(method);
  if (write && !csrfToken) await refreshCsrf();
  const retryInput = isRequestLike(input) ? input.clone() : input;

  const headers = new Headers(
    isRequestLike(input) ? input.headers : init?.headers,
  );
  if (write) headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(input, {
    ...init,
    credentials: "same-origin",
    headers,
  });

  if (write && retryCsrf && response.status === 403) {
    const error = (await response
      .clone()
      .json()
      .catch(() => null)) as { detail?: unknown } | null;
    if (typeof error?.detail === "string" && /csrf/i.test(error.detail)) {
      await refreshCsrf();
      return requestWithCsrf(retryInput, init, false);
    }
  }
  return response;
}

export const api = createClient<paths>({
  baseUrl: window.location.origin,
  credentials: "same-origin",
  fetch: requestWithCsrf,
});

type ApiResult<T> = {
  data?: T;
  error?: unknown;
  response: Response;
};

async function unwrap<T>(result: Promise<ApiResult<T>>): Promise<T> {
  const { data, error, response } = await result;
  if (error !== undefined) {
    const detail =
      typeof error === "object" && error && "detail" in error
        ? (error as { detail?: unknown }).detail
        : undefined;
    const code = sourceErrorCode(detail);
    throw new ApiError(
      response.status,
      typeof detail === "string"
        ? detail
        : response.statusText || "Request failed",
      code,
    );
  }
  return data as T;
}

export async function refreshCsrf() {
  const data = await unwrap(api.GET("/api/v1/auth/csrf"));
  csrfToken = data.csrf_token;
  return csrfToken;
}

export function setCsrf(token: string) {
  csrfToken = token;
}

async function oneTimePost<T>(path: string, body?: unknown): Promise<T> {
  // Authentication proofs can be consumed by the server. Never replay them.
  if (!csrfToken) await refreshCsrf();
  const submittedCsrf = csrfToken;
  const response = await fetch(`${window.location.origin}${path}`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": submittedCsrf },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    if (response.status === 403 && typeof payload?.detail === "string" && /csrf/i.test(payload.detail) && csrfToken === submittedCsrf) csrfToken = "";
    throw new ApiError(response.status, typeof payload?.detail === "string"
      ? payload.detail : response.statusText || "Request failed");
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const authApi = {
  setupStatus: () => unwrap(api.GET("/api/v1/setup/status")),
  me: () => unwrap(api.GET("/api/v1/auth/me")),
  createAdministrator: (body: components["schemas"]["CreateAdminInput"]) =>
    oneTimePost<components["schemas"]["AuthResponse"]>("/api/v1/setup/administrator", body),
  login: (body: components["schemas"]["LoginInput"]) =>
    oneTimePost<components["schemas"]["ChallengeResponse"] | components["schemas"]["AuthResponse"]>("/api/v1/auth/login", body),
  logout: () => unwrap(api.POST("/api/v1/auth/logout")),
  changePassword: (body: components["schemas"]["ChangePasswordInput"]) =>
    oneTimePost<void>("/api/v1/auth/password", body),
  twoFactorStatus: () => unwrap(api.GET("/api/v1/auth/two-factor")),
  verifyTwoFactor: (body: components["schemas"]["FactorProof"]) =>
    oneTimePost<components["schemas"]["AuthResponse"]>("/api/v1/auth/two-factor/verify", body),
  cancelTwoFactor: () => oneTimePost<void>("/api/v1/auth/two-factor/cancel"),
  enrollTwoFactor: (current_password: string) =>
    oneTimePost<components["schemas"]["EnrollmentResponse"]>("/api/v1/auth/two-factor/enroll", { current_password }),
  confirmTwoFactor: (body: components["schemas"]["ConfirmInput"]) =>
    oneTimePost<components["schemas"]["RecoveryCodesResponse"]>("/api/v1/auth/two-factor/confirm", body),
  disableTwoFactor: (body: components["schemas"]["ManagementInput"]) =>
    oneTimePost<void>("/api/v1/auth/two-factor/disable", body),
  regenerateRecoveryCodes: (body: components["schemas"]["ManagementInput"]) =>
    oneTimePost<components["schemas"]["RecoveryCodesResponse"]>("/api/v1/auth/two-factor/recovery-codes", body),
};

export const settingsApi = {
  get: () => unwrap(api.GET("/api/v1/settings")),
  preferences: (body: components["schemas"]["Preferences"]) =>
    unwrap(api.PUT("/api/v1/settings/preferences", { body })),
  onboarding: (body: components["schemas"]["Onboarding"]) =>
    unwrap(api.PUT("/api/v1/settings/onboarding", { body })),
  postgresql: (body: components["schemas"]["PostgreSQLInput"]) =>
    unwrap(api.PUT("/api/v1/settings/postgresql", { body })),
  testPostgresql: () => unwrap(api.POST("/api/v1/settings/postgresql/test")),
  mqtt: (body: components["schemas"]["MQTTInput"]) =>
    unwrap(api.PUT("/api/v1/settings/mqtt", { body })),
  testMqtt: () => unwrap(api.POST("/api/v1/settings/mqtt/test")),
  smtp: (body: components["schemas"]["SMTPInput"]) =>
    unwrap(api.PUT("/api/v1/settings/smtp", { body })),
  testSmtp: (body: components["schemas"]["SMTPTestInput"]) =>
    unwrap(api.POST("/api/v1/settings/smtp/test", { body })),
};

export type HistoryWindow = {
  vehicleId?: number;
  start: string;
  end: string;
  cursor?: string;
};

export type HistoryRequestOptions = {
  signal?: AbortSignal;
};

export const historyWindowPresets = ["today", "last_7_days", "last_30_days", "this_month", "this_year", "all_history", "custom"] as const;
export type HistoryWindowPreset = (typeof historyWindowPresets)[number];

function historyQuery(window: HistoryWindow, options?: HistoryRequestOptions) {
  return {
    signal: options?.signal,
    params: {
      query: {
        vehicle_id: window.vehicleId,
        start: window.start,
        end: window.end,
        limit: 50,
        cursor: window.cursor,
      },
    },
  };
}

export const historyApi = {
  vehicles: (options?: HistoryRequestOptions) =>
    unwrap(api.GET("/api/v1/vehicles", { signal: options?.signal })),
  historyWindow: (vehicleId: number, preset: HistoryWindowPreset = "last_30_days", options?: HistoryRequestOptions & { startDate?: string; endDate?: string }) =>
    unwrap(api.GET("/api/v1/vehicles/{vehicle_id}/history-window", {
      signal: options?.signal,
      params: { path: { vehicle_id: vehicleId }, query: { preset, start_date: options?.startDate, end_date: options?.endDate } },
    })),
  capabilities: (options?: HistoryRequestOptions) =>
    unwrap(api.GET("/api/v1/history/capabilities", { signal: options?.signal })),
  trips: (window: HistoryWindow, options?: HistoryRequestOptions) =>
    unwrap(api.GET("/api/v1/trips", historyQuery(window, options))),
  charges: (window: HistoryWindow, options?: HistoryRequestOptions) =>
    unwrap(api.GET("/api/v1/charges", historyQuery(window, options))),
  tripSummary: (window: HistoryWindow, options?: HistoryRequestOptions) =>
    unwrap(api.GET("/api/v1/vehicles/{vehicle_id}/trip-summary", {
      signal: options?.signal,
      params: {
        path: { vehicle_id: window.vehicleId! },
        query: { start: window.start, end: window.end },
      },
    })),
  chargeSummary: (window: HistoryWindow, options?: HistoryRequestOptions) =>
    unwrap(api.GET("/api/v1/vehicles/{vehicle_id}/charge-summary", {
      signal: options?.signal,
      params: {
        path: { vehicle_id: window.vehicleId! },
        query: { start: window.start, end: window.end },
      },
    })),
  trip: (tripId: number, options?: HistoryRequestOptions) =>
    unwrap(api.GET("/api/v1/trips/{trip_id}", { signal: options?.signal, params: { path: { trip_id: tripId } } })),
  charge: (chargeId: number, options?: HistoryRequestOptions) =>
    unwrap(api.GET("/api/v1/charges/{charge_id}", {
      signal: options?.signal,
      params: { path: { charge_id: chargeId } },
    })),
  trajectory: (tripId: number, options?: HistoryRequestOptions) =>
    unwrap(
      api.GET("/api/v1/trips/{trip_id}/trajectory", {
        signal: options?.signal,
        params: { path: { trip_id: tripId } },
      }),
    ),
};
