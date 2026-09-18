import "@mantine/core/styles.css";
import "@mantine/dates/styles.css";
import "./styles.css";
import "./i18n";

import {
  Alert,
  AppShell,
  Badge,
  Button,
  Checkbox,
  Container,
  Group,
  MantineProvider,
  Menu,
  Paper,
  PasswordInput,
  Select,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { DatesProvider } from "@mantine/dates";
import {
  QueryClient,
  QueryClientProvider,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { createRoot } from "react-dom/client";
import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import * as QRCode from "qrcode";
import "dayjs/locale/zh-cn";
import {
  BrowserRouter,
  Link,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ApiError, authApi, setCsrf, settingsApi } from "./api/client";
import {
  ChargeDetailPage,
  ChargesPage,
  TripDetailPage,
  TripsPage,
  VehiclesPage,
} from "./history";
import type { components } from "./api/schema";
import i18n from "./i18n";
import { PwaStatus, registerPwa, useOfflineVehicleDataGuard } from "./pwa";
import { HistoryContextProvider, useHistoryContext } from "./history-context";
import appTheme from "./theme";

type Settings = components["schemas"]["SettingsResponse"];
type Step = components["schemas"]["Onboarding"]["step"];
type PasswordAction = components["schemas"]["PasswordChange"]["action"];
type PostgreSQLInput = components["schemas"]["PostgreSQLInput"];
type MqttInput = components["schemas"]["MQTTInput"];
type SmtpInput = components["schemas"]["SMTPInput"];
type PostgreSQLResponse = components["schemas"]["PostgreSQLResponse"];
type ConnectionTestResult =
  | components["schemas"]["PostgreSQLTestResult"]
  | components["schemas"]["MQTTTestResult"]
  | components["schemas"]["SMTPTestResult"];
const steps: Step[] = ["preferences", "postgresql", "mqtt", "smtp", "two_factor", "review"];
const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: 0 } },
});
const browserTimezone =
  Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

function errorMessage(error: unknown, t: (key: string) => unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return String(t("sessionExpired"));
    if (error.status === 422) return String(t("requestFailed"));
  }
  return String(t("requestFailed"));
}

function factorErrorMessage(error: unknown, t: (key: string) => unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) return String(t("twoFactorRateLimited"));
    if (error.status === 401 && /challenge expired/i.test(error.message))
      return String(t("twoFactorChallengeExpired"));
    if (error.status === 401 && /verification code/i.test(error.message))
      return String(t("twoFactorInvalidCode"));
    if (error.status === 409 && /enrollment expired/i.test(error.message))
      return String(t("twoFactorEnrollmentExpired"));
    if (error.status === 401 && /current password/i.test(error.message))
      return String(t("invalidCurrentPassword"));
    if (error.status === 503) return String(t("twoFactorUnavailable"));
  }
  return errorMessage(error, t);
}

function testErrorMessage(
  kind: Kind,
  error: unknown,
  t: (key: string) => unknown,
): string {
  if (kind !== "smtp") return errorMessage(error, t);
  if (error instanceof ApiError) {
    if (error.status === 401) return String(t("sessionExpired"));
    if (error.status === 422) return String(t("smtpTestRejected"));
    if (error.status < 500) return String(t("smtpTestFailed"));
  }
  return String(t("smtpTestUnknown"));
}

function useSettings() {
  return useQuery({
    queryKey: ["settings"],
    queryFn: async () => {
      const settings = await settingsApi.get();
      if (settings.preferences?.saved) {
        await i18n.changeLanguage(settings.preferences.language);
      }
      return settings;
    },
  });
}
function statusKey(status: string) {
  return `status${status.slice(0, 1).toUpperCase()}${status.slice(1)}`;
}

function testCodeKey(kind: Kind, code: string) {
  if (kind !== "postgresql") {
    const shared = new Set([
      "unconfigured",
      "disabled",
      "skipped",
      "invalid_credentials",
      "unavailable",
      "timeout",
    ]);
    if (shared.has(code)) {
      const camelCase = code.replace(/_([a-z])/g, (_, letter: string) =>
        letter.toUpperCase(),
      );
      return `testCodeService${camelCase.slice(0, 1).toUpperCase()}${camelCase.slice(1)}`;
    }
  }
  const camelCase = code.replace(/_([a-z])/g, (_, letter: string) =>
    letter.toUpperCase(),
  );
  return `testCode${camelCase.slice(0, 1).toUpperCase()}${camelCase.slice(1)}`;
}

function LanguageButton() {
  const { t } = useTranslation();
  return (
    <Button
      variant="subtle"
      onClick={() =>
        void i18n.changeLanguage(i18n.language.startsWith("zh") ? "en" : "zh")
      }
    >
      {t("language")}
    </Button>
  );
}

function Credentials() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const client = useQueryClient();
  const setup = useQuery({
    queryKey: ["setup"],
    queryFn: async () => {
      const data = await authApi.setupStatus();
      setCsrf(data.csrf_token);
      return data;
    },
  });
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [confirmationMismatch, setConfirmationMismatch] = useState(false);
  const [twoFactorRequired, setTwoFactorRequired] = useState(false);
  const [useRecoveryCode, setUseRecoveryCode] = useState(false);
  const challengeActive = useRef(false);
  const operation = useOperation();
  const cancellation = useRef<Promise<unknown>>(Promise.resolve());
  const [passwordPending, setPasswordPending] = useState(false);
  const [passwordError, setPasswordError] = useState<unknown>(null);
  const [verifyPending, setVerifyPending] = useState(false);
  const [verifyError, setVerifyError] = useState<unknown>(null);
  const cancel = () => {
    operation.cancel();
    setPassword(""); setConfirmation(""); setVerifyError(null); setVerifyPending(false); setUseRecoveryCode(false);
    setTwoFactorRequired(false);
    if (challengeActive.current) {
      challengeActive.current = false;
      cancellation.current = authApi.cancelTwoFactor().catch(() => undefined);
    }
  };
  useEffect(() => () => {
    if (challengeActive.current) void authApi.cancelTwoFactor().catch(() => undefined);
  }, []);
  if (setup.isPending) return <Busy />;
  if (!setup.isSuccess || !setup.data)
    return (
      <ReadFailure
        retry={() => void setup.refetch()}
        pending={setup.isFetching}
        showLanguage
      />
    );
  const creating = setup.data.administrator_exists === false;
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (passwordPending) return;
    const values = new FormData(event.currentTarget);
    const formUsername = String(values.get("username") ?? "");
    const formPassword = String(values.get("password") ?? "");
    const formConfirmation = String(values.get("password_confirmation") ?? "");
    const mismatch = creating && formPassword !== formConfirmation;
    setConfirmationMismatch(mismatch);
    if (mismatch) return;
    const live = operation.start();
    setPasswordPending(true);
    setPasswordError(null);
    if (creating) {
      void cancellation.current.then(() => authApi.createAdministrator({ username: formUsername, password: formPassword, password_confirmation: formConfirmation }))
        .then((data) => { if (!live()) return; setCsrf(data.csrf_token); void client.invalidateQueries({ queryKey: ["setup"] }); navigate("/setup", { replace: true }); })
        .catch((error: unknown) => { if (live()) setPasswordError(error); })
        .finally(() => { if (live()) setPasswordPending(false); });
    } else {
      void cancellation.current.then(() => authApi.login({ username: formUsername, password: formPassword }))
        .then((data) => { if (!live()) return; setPassword(""); setConfirmation(""); client.removeQueries({ predicate: (query) => query.queryKey[0] !== "setup" }); setCsrf(data.csrf_token); if (data.status === "two_factor_required") { challengeActive.current = true; setTwoFactorRequired(true); } else navigate("/", { replace: true }); })
        .catch((error: unknown) => { if (live()) setPasswordError(error); })
        .finally(() => { if (live()) setPasswordPending(false); });
    }
  };
  const error = passwordError;
  return (
    <Container size="xs" py="xl">
      <Stack gap="lg">
        <Group justify="space-between">
          <Title order={1}>{t("appName")}</Title>
          <LanguageButton />
        </Group>
        <Paper withBorder radius="lg" p="xl">
          <Stack>
            <Title order={2}>{creating ? t("createTitle") : twoFactorRequired ? t("twoFactorSignIn") : t("loginTitle")}</Title>
            {creating && <Text c="dimmed">{t("createIntro")}</Text>}
            {!twoFactorRequired && <form method="post" onSubmit={submit} noValidate={false}>
              <Stack>
                <TextInput
                  id="credentials-username"
                  name="username"
                  label={t("username")}
                  defaultValue={username}
                  onChange={(e) => setUsername(e.currentTarget.value)}
                  autoComplete="username"
                  spellCheck={false}
                  autoCapitalize="none"
                  required
                />
                <PasswordInput
                  id="credentials-password"
                  name="password"
                  label={t("password")}
                  defaultValue={password}
                  onChange={(e) => setPassword(e.currentTarget.value)}
                  autoComplete={creating ? "new-password" : "current-password"}
                  required
                />
                {creating && (
                  <>
                    <PasswordInput
                      id="credentials-password-confirmation"
                      name="password_confirmation"
                      label={t("confirmPassword")}
                      defaultValue={confirmation}
                      onChange={(e) => {
                        setConfirmation(e.currentTarget.value);
                        setConfirmationMismatch(false);
                      }}
                      autoComplete="new-password"
                      error={
                        confirmationMismatch ||
                        (confirmation && password !== confirmation)
                          ? t("passwordsMismatch")
                          : undefined
                      }
                      required
                    />
                    <Text size="sm" c="dimmed">
                      {t("passwordMin")}
                    </Text>
                  </>
                )}
                {Boolean(error) && <Alert color="red">{errorMessage(error, t)}</Alert>}
                <Button type="submit" loading={passwordPending}>
                  {creating ? t("create") : t("signIn")}
                </Button>
              </Stack>
            </form>}
            {twoFactorRequired && <form method="post" onSubmit={(event) => {
              event.preventDefault();
              if (verifyPending) return;
              const code = String(new FormData(event.currentTarget).get("two_factor_code") ?? "");
              const live = operation.start();
              event.currentTarget.reset();
              setVerifyPending(true); setVerifyError(null);
              void authApi.verifyTwoFactor({ method: useRecoveryCode ? "recovery_code" : "totp", code }).then((data) => {
                if (!live()) return;
                challengeActive.current = false; setCsrf(data.csrf_token); navigate("/", { replace: true });
              }).catch((error: unknown) => { if (live()) setVerifyError(error); }).finally(() => { if (live()) setVerifyPending(false); });
            }}>
              <Stack>
                <Text c="dimmed">{t(useRecoveryCode ? "recoveryCodeSignInHelp" : "twoFactorSignInHelp")}</Text>
                <TextInput
                  type="text"
                  key={String(useRecoveryCode)}
                  id="login-two-factor-code"
                  name="two_factor_code"
                  label={t(useRecoveryCode ? "recoveryCode" : "verificationCode")}
                  autoComplete="one-time-code"
                  inputMode={useRecoveryCode ? "text" : "numeric"}
                  pattern={useRecoveryCode ? undefined : "[0-9]{6}"}
                  minLength={useRecoveryCode ? undefined : 6}
                  maxLength={useRecoveryCode ? 64 : 6}
                  autoCapitalize="none"
                  spellCheck={false}
                  required
                />
                {Boolean(verifyError) && <Alert color="red">{factorErrorMessage(verifyError, t)}</Alert>}
                <Button type="submit" loading={verifyPending}>{t("verify")}</Button>
                <Button type="button" variant="subtle" disabled={verifyPending} onClick={() => { setUseRecoveryCode((value) => !value); setVerifyError(null); }}>
                  {t(useRecoveryCode ? "useAuthenticatorCode" : "useRecoveryCode")}
                </Button>
                <Button type="button" variant="subtle" color="gray" onClick={cancel}>{t("cancel")}</Button>
              </Stack>
            </form>}
          </Stack>
        </Paper>
      </Stack>
    </Container>
  );
}

function Busy() {
  const { t } = useTranslation();
  return (
    <Container py="xl">
      <Text>{t("loading")}</Text>
    </Container>
  );
}

function PasswordChoice({
  passwordSet,
  action,
  setAction,
  value,
  setValue,
}: {
  passwordSet: boolean;
  action: PasswordAction;
  setAction: (v: PasswordAction) => void;
  value: string;
  setValue: (v: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <Stack gap="xs">
      <Select
        label={t("passwordAction")}
        value={action}
        onChange={(v) => setAction((v ?? "retain") as PasswordAction)}
        data={[
          {
            value: "retain",
            label: passwordSet ? t("passwordRetain") : t("noPassword"),
          },
          { value: "replace", label: t("passwordReplace") },
          { value: "clear", label: t("passwordClear") },
        ]}
      />
      {action === "replace" && (
        <PasswordInput
          label={t("passwordOptional")}
          value={value}
          onChange={(e) => setValue(e.currentTarget.value)}
          required
        />
      )}
    </Stack>
  );
}

function Preferences({
  settings,
  afterSave,
}: {
  settings: Settings;
  afterSave?: () => void;
}) {
  const { t } = useTranslation();
  const pref = settings.preferences!;
  const [language, setLanguage] = useState(
    pref.saved ? pref.language : i18n.language.startsWith("zh") ? "zh" : "en",
  );
  const [timezone, setTimezone] = useState(
    pref.saved ? pref.timezone : browserTimezone,
  );
  const [tileUrl, setTileUrl] = useState(pref.tile_url);
  const save = useMutation({
    mutationFn: () =>
      settingsApi.preferences({ language, timezone, tile_url: tileUrl }),
    onSuccess: (data) => {
      void i18n.changeLanguage(language);
      queryClient.setQueryData(["settings"], data);
      afterSave?.();
    },
  });
  return (
    <FormCard title={t("preferences")} error={save.error}>
      <Select
        label={t("languageLabel")}
        value={language}
        onChange={(v) => setLanguage((v ?? "en") as "en" | "zh")}
        data={[
          { value: "en", label: "English" },
          { value: "zh", label: "中文" },
        ]}
      />
      <TextInput
        label={t("timezone")}
        value={timezone}
        onChange={(e) => setTimezone(e.currentTarget.value)}
        required
      />
      <TextInput
        label={t("tileUrl")}
        value={tileUrl}
        onChange={(e) => setTileUrl(e.currentTarget.value)}
      />
      <Button onClick={() => save.mutate()} loading={save.isPending}>
        {t("save")}
      </Button>
    </FormCard>
  );
}

type Kind = "postgresql" | "mqtt" | "smtp";

function saveConnection(
  kind: Kind,
  input: PostgreSQLInput | MqttInput | SmtpInput,
) {
  if (kind === "postgresql")
    return settingsApi.postgresql(input as PostgreSQLInput);
  if (kind === "mqtt") return settingsApi.mqtt(input as MqttInput);
  return settingsApi.smtp(input as SmtpInput);
}

function savedConnectionInput(kind: Kind, data: Settings[Kind]) {
  if (kind === "postgresql") {
    const value = data as PostgreSQLResponse;
    return { host: value.host, username: value.username, port: value.port, enabled: value.enabled, skipped: value.skipped, database: value.database, sslmode: value.sslmode };
  }
  if (kind === "mqtt") {
    const value = data as Settings["mqtt"];
    return { host: value!.host, username: value!.username, port: value!.port, enabled: value!.enabled, skipped: value!.skipped, tls: value!.tls, verify_tls: value!.verify_tls, topic_prefix: value!.topic_prefix };
  }
  const value = data as Settings["smtp"];
  return { host: value!.host, username: value!.username, port: value!.port, enabled: value!.enabled, skipped: value!.skipped, tls_mode: value!.tls_mode, verify_tls: value!.verify_tls, sender: value!.sender };
}

function Connection({
  kind,
  settings,
  afterSave,
}: {
  kind: Kind;
  settings: Settings;
  afterSave?: () => void;
}) {
  const { t } = useTranslation();
  const data = settings[kind]!;
  const [host, setHost] = useState(data.host);
  const [username, setUsername] = useState(data.username);
  const [port, setPort] = useState(String(data.port));
  const [enabled, setEnabled] = useState(data.enabled);
  const [action, setAction] = useState<PasswordAction>("retain");
  const [password, setPassword] = useState("");
  const [recipient, setRecipient] = useState("");
  const [extra, setExtra] = useState<Record<string, string | boolean>>(
    kind === "postgresql"
      ? {
          database: settings.postgresql!.database,
          sslmode: settings.postgresql!.sslmode,
        }
      : kind === "mqtt"
        ? {
            tls: settings.mqtt!.tls,
            verify_tls: settings.mqtt!.verify_tls,
            topic_prefix: settings.mqtt!.topic_prefix,
          }
        : {
            tls_mode: settings.smtp!.tls_mode,
            verify_tls: settings.smtp!.verify_tls,
            sender: settings.smtp!.sender,
          },
  );
  const input = (skipped: boolean) => ({
    host,
    username,
    port: Number(port),
    enabled: skipped ? false : enabled,
    skipped,
    ...extra,
    password: {
      action: skipped ? "retain" : action,
      ...(!skipped && action === "replace" ? { value: password } : {}),
    },
  });
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(
    data.test_result ?? null,
  );
  const savedTestResult =
    data.test_result?.version === data.version
      ? data.test_result
      : null;
  const localTestResult = testResult?.version === data.version ? testResult : null;
  const displayedTestResult =
    testResult?.persisted === false
      ? testResult
      : savedTestResult &&
          localTestResult &&
          new Date(savedTestResult.tested_at) > new Date(localTestResult.tested_at)
        ? savedTestResult
        : localTestResult ?? savedTestResult;
  const save = useMutation({
    mutationFn: () =>
      saveConnection(
        kind,
        input(false) as PostgreSQLInput | MqttInput | SmtpInput,
      ),
    onSuccess: (result) => {
      test.reset();
      queryClient.setQueryData(["settings"], result);
      setPassword("");
      setAction("retain");
      setTestResult(null);
      afterSave?.();
    },
  });
  const persistedInput = savedConnectionInput(kind, data);
  const currentInput = {
    host,
    username,
    port: Number(port),
    enabled,
    skipped: false,
    ...extra,
  };
  const hasUnsavedChanges =
    JSON.stringify(currentInput) !== JSON.stringify(persistedInput) ||
      action !== "retain" ||
      Boolean(password);
  const recipientValid =
    recipient.length <= 320 &&
    /^[\x21-\x7e]+$/.test(recipient) &&
    /^[^\s@,]+@[^\s@,]+$/.test(recipient);
  const test = useMutation<ConnectionTestResult>({
    mutationFn: async () => {
      const result =
        kind === "postgresql"
          ? await settingsApi.testPostgresql()
          : kind === "mqtt"
            ? await settingsApi.testMqtt()
            : await settingsApi.testSmtp({ recipient });
      return result;
    },
    onMutate: () => {
      setTestResult(null);
    },
    onSuccess: async (result) => {
      setTestResult(result);
      try {
        const refreshed = await settingsApi.get();
        queryClient.setQueryData(["settings"], refreshed);
        const confirmed = refreshed[kind]!;
        if (confirmed) {
          setHost(confirmed.host);
          setUsername(confirmed.username);
          setPort(String(confirmed.port));
          setEnabled(confirmed.enabled);
          if (kind === "postgresql") {
            const postgresql = confirmed as PostgreSQLResponse;
            setExtra({ database: postgresql.database, sslmode: postgresql.sslmode });
          } else if (kind === "mqtt") {
            const mqtt = confirmed as Settings["mqtt"];
            setExtra({ tls: mqtt!.tls, verify_tls: mqtt!.verify_tls, topic_prefix: mqtt!.topic_prefix });
          } else {
            const smtp = confirmed as Settings["smtp"];
            setExtra({ tls_mode: smtp!.tls_mode, verify_tls: smtp!.verify_tls, sender: smtp!.sender });
          }
          setAction("retain");
          setPassword("");
        }
      } catch {
        // Keep the structured test result visible when only the follow-up refresh fails.
      }
      setTestResult(result);
    },
  });
  const displayedStatus =
    test.isPending
      ? "unverified"
      : test.error
        ? "failure"
        : displayedTestResult
          ? displayedTestResult.status
          : data.status;
  const skip = useMutation({
    mutationFn: () =>
      saveConnection(
        kind,
        input(true) as PostgreSQLInput | MqttInput | SmtpInput,
      ),
    onSuccess: (result) => {
      test.reset();
      queryClient.setQueryData(["settings"], result);
      const confirmed = result[kind]!;
      setHost(confirmed.host);
      setUsername(confirmed.username);
      setPort(String(confirmed.port));
      setEnabled(confirmed.enabled);
      if (kind === "postgresql") {
        const postgresql = result.postgresql!;
        setExtra({
          database: postgresql.database,
          sslmode: postgresql.sslmode,
        });
      } else if (kind === "mqtt") {
        const mqtt = result.mqtt!;
        setExtra({
          tls: mqtt.tls,
          verify_tls: mqtt.verify_tls,
          topic_prefix: mqtt.topic_prefix,
        });
      } else {
        const smtp = result.smtp!;
        setExtra({
          tls_mode: smtp.tls_mode,
          verify_tls: smtp.verify_tls,
          sender: smtp.sender,
        });
      }
      setAction("retain");
      setPassword("");
      setTestResult(null);
      afterSave?.();
    },
  });
  const set = (key: string, value: string | boolean) =>
    setExtra((old) => ({ ...old, [key]: value }));
  const heading = t(kind);
  return (
    <FormCard title={heading} error={save.error ?? skip.error}>
      <Group justify="space-between">
        <Badge
          color={
            displayedStatus === "success"
              ? "green"
              : displayedStatus === "failure"
                ? "red"
                : displayedStatus === "unverified"
                  ? "yellow"
                  : "gray"
          }
        >
          {t(statusKey(displayedStatus))}
        </Badge>
        <Text size="sm" c="dimmed">
          {data.password_set ? t("passwordSaved") : t("noPassword")}
        </Text>
      </Group>
      {kind === "postgresql" && (
        <Text size="sm" c="dimmed">
          {t("pgHelp")}
        </Text>
      )}
      <fieldset
        disabled={test.isPending}
        style={{
          border: 0,
          padding: 0,
          margin: 0,
          display: "flex",
          flexDirection: "column",
          gap: "var(--mantine-spacing-md)",
        }}
      >
        <TextInput
          label={t("host")}
          value={host}
          onChange={(e) => setHost(e.currentTarget.value)}
        />
        <TextInput
          label={t("port")}
          type="number"
          value={port}
          onChange={(e) => setPort(e.currentTarget.value)}
        />
        <TextInput
          label={t("username")}
          value={username}
          onChange={(e) => setUsername(e.currentTarget.value)}
        />
        {kind === "postgresql" && (
          <>
            <TextInput
              label={t("database")}
              value={String(extra.database)}
              onChange={(e) => set("database", e.currentTarget.value)}
            />
            <Select
              label={t("sslMode")}
              value={String(extra.sslmode)}
              onChange={(v) => set("sslmode", v ?? "prefer")}
              data={[
                "disable",
                "allow",
                "prefer",
                "require",
                "verify-ca",
                "verify-full",
              ]}
            />
          </>
        )}
      {kind === "mqtt" && (
        <>
          <Checkbox
            label={t("tls")}
            checked={Boolean(extra.tls)}
            onChange={(e) => set("tls", e.currentTarget.checked)}
          />
          <Checkbox
            label={t("verifyTls")}
            checked={Boolean(extra.verify_tls)}
            onChange={(e) => set("verify_tls", e.currentTarget.checked)}
          />
          <TextInput
            label={t("topicPrefix")}
            value={String(extra.topic_prefix)}
            onChange={(e) => set("topic_prefix", e.currentTarget.value)}
          />
        </>
      )}
      {kind === "smtp" && (
        <>
          <Select
            label={t("tlsMode")}
            value={String(extra.tls_mode)}
            onChange={(v) => set("tls_mode", v ?? "starttls")}
            data={["implicit", "starttls", "plain"]}
          />
          <Checkbox
            label={t("verifyTls")}
            checked={Boolean(extra.verify_tls)}
            onChange={(e) => set("verify_tls", e.currentTarget.checked)}
          />
          <TextInput
            label={t("sender")}
            value={String(extra.sender)}
            onChange={(e) => set("sender", e.currentTarget.value)}
          />
        </>
      )}
      <Checkbox
        label={t("enabled")}
        checked={enabled}
        onChange={(e) => setEnabled(e.currentTarget.checked)}
      />
      <PasswordChoice
        passwordSet={data.password_set}
        action={action}
        setAction={setAction}
        value={password}
        setValue={setPassword}
      />
      <Alert color="gray">
        {t(
          kind === "postgresql"
            ? "pgHelpShort"
            : kind === "mqtt"
              ? "mqttHelp"
              : "smtpHelp",
        )}
      </Alert>
      <Stack gap="xs">
        {kind === "smtp" && (
          <TextInput
            label={t("smtpRecipient")}
            description={t("smtpRecipientHelp")}
            type="email"
            value={recipient}
            onChange={(e) => setRecipient(e.currentTarget.value)}
            required
          />
        )}
        <Button
          variant="light"
          onClick={() => test.mutate()}
          loading={test.isPending}
          disabled={hasUnsavedChanges || (kind === "smtp" && !recipientValid)}
        >
          {test.isPending
            ? t(
                kind === "smtp"
                  ? "testingSmtp"
                  : kind === "mqtt"
                    ? "testingMqtt"
                    : "testingConnection",
              )
            : t(
                kind === "smtp"
                  ? "testSmtp"
                  : kind === "mqtt"
                    ? "testMqtt"
                    : "testConnection",
              )}
        </Button>
        {hasUnsavedChanges && (
          <Text size="sm" c="dimmed">
            {t("saveFirstToTest")}
          </Text>
        )}
        {kind === "smtp" && !recipientValid && (
          <Text size="sm" c="dimmed">
            {t("validRecipientRequired")}
          </Text>
        )}
        {test.error && <Alert color="red">{testErrorMessage(kind, test.error, t)}</Alert>}
        {!test.error && !test.isPending && displayedTestResult && (
          <Alert
            color={displayedTestResult.status === "success" ? "green" : "red"}
          >
            <Stack gap={2}>
              <Text fw={600}>
                {t(
                  displayedTestResult.status === "success"
                    ? "testSuccess"
                    : "testFailure",
                )}
              </Text>
              <Text>{t(testCodeKey(kind, displayedTestResult.code))}</Text>
              {kind === "mqtt" && "message_received" in displayedTestResult && (
                <Text>
                  {t(
                    displayedTestResult.message_received
                      ? "mqttMessageReceived"
                      : "mqttNoMessage",
                  )}
                </Text>
              )}
              {kind === "smtp" &&
                "delivery_accepted" in displayedTestResult &&
                displayedTestResult.code === "configuration_changed" &&
                displayedTestResult.delivery_accepted && (
                  <Text>{t("configurationChangedExternalAction")}</Text>
                )}
              {kind === "smtp" &&
                displayedTestResult.status === "failure" &&
                ["timeout", "unavailable", "protocol_error", "tls_error"].includes(
                  displayedTestResult.code,
                ) && <Text>{t("smtpDeliveryUncertain")}</Text>}
              <Text size="sm" c="dimmed">
                {t("testAt")}: {" "}
                {new Intl.DateTimeFormat(undefined, {
                  dateStyle: "medium",
                  timeStyle: "short",
                  timeZone: settings.preferences?.timezone || "UTC",
                }).format(new Date(displayedTestResult.tested_at))}
              </Text>
            </Stack>
          </Alert>
        )}
      </Stack>
      <Group grow>
        <Button onClick={() => save.mutate()} loading={save.isPending}>
          {t("save")}
        </Button>
        <Button
          variant="light"
          onClick={() => skip.mutate()}
          loading={skip.isPending}
        >
          {t("skip")}
        </Button>
      </Group>
      </fieldset>
    </FormCard>
  );
}

function FormCard({
  title,
  children,
  error,
  factorError = false,
}: {
  title: string;
  children: React.ReactNode;
  error?: unknown;
  factorError?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <Paper component="section" aria-label={title} withBorder radius="lg" p="xl">
      <Stack>
        <Title order={2}>{title}</Title>
        {Boolean(error) && <Alert color="red">{factorError ? factorErrorMessage(error, t) : errorMessage(error, t)}</Alert>}
        {children}
      </Stack>
    </Paper>
  );
}

// A cancelled operation can finish after another form has opened. Only the
// current generation may publish its result, including CSRF and navigation.
function useOperation() {
  const generation = useRef(0);
  useEffect(() => () => { generation.current += 1; }, []);
  return useMemo(() => ({
    start: () => { const id = ++generation.current; return () => generation.current === id; },
    cancel: () => { generation.current += 1; },
  }), []);
}

function refreshAfterFactorRotation(client: QueryClient) {
  // Discard data from the old session without unmounting the one-time code view.
  client.removeQueries({ predicate: (query) => !["me", "settings", "two-factor"].includes(String(query.queryKey[0])) });
  void client.invalidateQueries({ queryKey: ["me"] });
  void client.invalidateQueries({ queryKey: ["settings"] });
}

type FactorProof = components["schemas"]["FactorProof"];

function FactorProofInputs({ id, method, setMethod, disabled = false }: {
  id: string;
  disabled?: boolean;
  method: FactorProof["method"];
  setMethod: (method: FactorProof["method"]) => void;
}) {
  const { t } = useTranslation();
  return <>
    <Select
      disabled={disabled}
      id={`${id}-method`}
      name={`${id.replace("-", "_")}_method`}
      label={t("verificationMethod")}
      value={method}
      onChange={(value) => setMethod(value === "recovery_code" ? "recovery_code" : "totp")}
      data={[{ value: "totp", label: t("authenticatorCode") }, { value: "recovery_code", label: t("recoveryCode") }]}
    />
    <TextInput
      key={method}
      id={`${id}-code`}
      name={`${id.replace("-", "_")}_code`}
      label={t(method === "totp" ? "verificationCode" : "recoveryCode")}
      autoComplete="one-time-code"
      inputMode={method === "totp" ? "numeric" : "text"}
      pattern={method === "totp" ? "[0-9]*" : undefined}
      autoCapitalize="none"
      spellCheck={false}
      required
    />
  </>;
}

function RecoveryCodes({ codes, done }: { codes: string[]; done: () => void }) {
  const { t } = useTranslation();
  return <FormCard title={t("recoveryCodes")}>
    <Alert color="yellow">{t("recoveryCodesOnce")}</Alert>
    <Text component="pre" style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{codes.join("\n")}</Text>
    <Button onClick={done}>{t("recoveryCodesSaved")}</Button>
  </FormCard>;
}

function TwoFactorEnrollment({ done, showRecoveryCodes }: { done: () => void; showRecoveryCodes: (codes: string[]) => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [enrollment, setEnrollment] = useState<components["schemas"]["EnrollmentResponse"] | null>(null);
  const [qr, setQr] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const operation = useOperation();
  useEffect(() => {
    if (!enrollment) return;
    let live = true;
    void QRCode.toDataURL(enrollment.provisioning_uri, { margin: 1, width: 256 })
      .then((url) => { if (live) setQr(url); })
      .catch(() => { if (live) setQr(""); });
    const expiry = window.setTimeout(() => {
      operation.cancel(); setPending(false); setEnrollment(null); setQr(""); setError(new ApiError(409, "Two-factor enrollment expired or invalid"));
    }, enrollment.expires_in * 1000);
    return () => { live = false; window.clearTimeout(expiry); };
  }, [enrollment, operation]);
  const cancel = () => {
    operation.cancel(); setEnrollment(null); setQr(""); setError(null); setPending(false); done();
  };
  if (!enrollment) return <FormCard title={t("enableTwoFactor")} error={error} factorError>
    <form method="post" onSubmit={(event) => {
      event.preventDefault();
      if (pending) return;
      const live = operation.start();
      const password = String(new FormData(event.currentTarget).get("current_password") ?? "");
      event.currentTarget.reset();
      setPending(true); setError(null);
      void authApi.enrollTwoFactor(password)
        .then((data) => { if (live()) setEnrollment(data); })
        .catch((reason: unknown) => { if (live()) setError(reason); })
        .finally(() => { if (live()) setPending(false); });
    }}><Stack>
      <Text c="dimmed">{t("twoFactorEnrollHelp")}</Text>
      <PasswordInput id="two-factor-enroll-password" name="current_password" label={t("currentPassword")} autoComplete="current-password" required />
      <Group grow><Button type="submit" loading={pending}>{t("continue")}</Button><Button type="button" variant="light" onClick={cancel}>{t("cancel")}</Button></Group>
    </Stack></form>
  </FormCard>;
  return <FormCard title={t("scanAuthenticator")} error={error} factorError>
    <Stack>
      <Text c="dimmed">{t("twoFactorEnrollmentExpiry")}</Text>
      {qr ? <img src={qr} alt={t("twoFactorQrAlt")} width="256" height="256" style={{ maxWidth: "100%", height: "auto" }} /> : <Text>{t("loading")}</Text>}
      <Text style={{ overflowWrap: "anywhere" }}>{t("manualSecret")}: <code>{enrollment.secret}</code></Text>
      <form method="post" onSubmit={(event) => {
        event.preventDefault();
        if (pending) return;
        const values = new FormData(event.currentTarget);
        const live = operation.start();
        event.currentTarget.reset();
        setPending(true); setError(null);
        void authApi.confirmTwoFactor({ current_password: String(values.get("current_password") ?? ""), code: String(values.get("verification_code") ?? "") })
          .then((data) => {
            if (!live()) return;
            setCsrf(data.csrf_token); setEnrollment(null); setQr(""); showRecoveryCodes(data.recovery_codes);
            refreshAfterFactorRotation(qc);
          })
          .catch((reason: unknown) => {
            if (!live()) return;
            setError(reason);
            if (reason instanceof ApiError && reason.status === 409) { setEnrollment(null); setQr(""); }
          })
          .finally(() => { if (live()) setPending(false); });
      }}><Stack>
        <PasswordInput id="two-factor-confirm-password" name="current_password" label={t("currentPassword")} autoComplete="current-password" required />
        <TextInput id="two-factor-confirm-code" name="verification_code" label={t("verificationCode")} autoComplete="one-time-code" inputMode="numeric" pattern="[0-9]{6}" minLength={6} maxLength={6} required />
        <Group grow><Button type="submit" loading={pending}>{t("enableTwoFactor")}</Button><Button type="button" variant="light" onClick={cancel}>{t("cancel")}</Button></Group>
      </Stack></form>
    </Stack>
  </FormCard>;
}

function TwoFactorSettings({ afterContinue }: { afterContinue?: () => void } = {}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const status = useQuery({ queryKey: ["two-factor"], queryFn: authApi.twoFactorStatus });
  const [mode, setMode] = useState<"idle" | "enroll" | "disable" | "recovery">("idle");
  const [method, setMethod] = useState<FactorProof["method"]>("totp");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [managementPending, setManagementPending] = useState(false);
  const [managementError, setManagementError] = useState<unknown>(null);
  const operation = useOperation();
  const finishEnrollment = () => {
    setCodes(null); setMode("idle");
    void qc.invalidateQueries({ queryKey: ["two-factor"] });
  };
  // Local enrollment owns its lifetime, including while confirmation is pending.
  // Background status success or failure must not discard the one-time response.
  if (codes) return <RecoveryCodes codes={codes} done={finishEnrollment} />;
  if (mode === "enroll") return <Stack>
    <TwoFactorEnrollment done={finishEnrollment} showRecoveryCodes={setCodes} />
    {afterContinue && <Button variant="light" onClick={afterContinue}>{t("skip")}</Button>}
  </Stack>;
  if (status.isPending) return <Busy />;
  if (status.error || !status.data) return <FormCard title={t("twoFactorAuthentication")} error={status.error}><Text>{t("requestFailed")}</Text></FormCard>;
  if (!status.data.enabled) return <Stack>
    <FormCard title={t("twoFactorAuthentication")}><Text>{t("twoFactorDisabled")}</Text><Button onClick={() => setMode("enroll")}>{t("enableTwoFactor")}</Button></FormCard>
    {afterContinue && <Button variant="light" onClick={afterContinue}>{t("skip")}</Button>}
  </Stack>;
  if (mode === "idle") return <FormCard title={t("twoFactorAuthentication")}>
    <Text>{t("twoFactorEnabled")}</Text>
    <Text>{t("recoveryCodesRemaining", { count: status.data.recovery_codes_remaining })}</Text>
    {status.data.recovery_codes_remaining === 0 && <Alert color="yellow">{t("recoveryCodesEmpty")}</Alert>}
    {afterContinue ? <Button onClick={afterContinue}>{t("continue")}</Button>
      : <Group grow><Button variant="light" onClick={() => setMode("recovery")}>{t("regenerateRecoveryCodes")}</Button><Button color="red" variant="light" onClick={() => setMode("disable")}>{t("disableTwoFactor")}</Button></Group>}
  </FormCard>;
  return <FormCard title={mode === "disable" ? t("disableTwoFactor") : t("regenerateRecoveryCodes")} error={managementError} factorError>
    <form method="post" onSubmit={(event) => {
      event.preventDefault();
      if (managementPending) return;
      const values = new FormData(event.currentTarget);
      const body = { current_password: String(values.get("current_password") ?? ""), proof: { method, code: String(values.get("management_code") ?? "") } };
      const live = operation.start();
      event.currentTarget.reset();
      setManagementPending(true); setManagementError(null);
      const action = mode === "disable" ? authApi.disableTwoFactor(body) : authApi.regenerateRecoveryCodes(body);
      void action.then((data) => {
        if (!live()) return;
        if (mode === "disable") { qc.clear(); navigate("/login"); return; }
        const response = data as components["schemas"]["RecoveryCodesResponse"];
        setCsrf(response.csrf_token); setCodes(response.recovery_codes);
        refreshAfterFactorRotation(qc);
        void qc.invalidateQueries({ queryKey: ["two-factor"] });
      }).catch((reason: unknown) => { if (live()) setManagementError(reason); })
        .finally(() => { if (live()) setManagementPending(false); });
    }}><Stack>
      <Alert color="yellow">{t(mode === "disable" ? "disableTwoFactorNotice" : "regenerateRecoveryCodesNotice")}</Alert>
      <PasswordInput id="two-factor-management-password" name="current_password" label={t("currentPassword")} autoComplete="current-password" required />
      <FactorProofInputs id="management" method={method} setMethod={setMethod} disabled={managementPending} />
      <Group grow><Button type="submit" color={mode === "disable" ? "red" : undefined} loading={managementPending}>{t("confirm")}</Button><Button type="button" variant="light" onClick={() => { operation.cancel(); setMode("idle"); setMethod("totp"); setManagementPending(false); setManagementError(null); void qc.invalidateQueries({ queryKey: ["two-factor"] }); }}>{t("cancel")}</Button></Group>
    </Stack></form>
  </FormCard>;
}

function TwoFactorOnboarding({ afterContinue }: { afterContinue: () => void }) {
  return <TwoFactorSettings afterContinue={afterContinue} />;
}

function Setup() {
  const { t } = useTranslation();
  const settings = useSettings();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const transition = useMutation({
    mutationFn: ({ step, completed }: { step: Step; completed: boolean }) =>
      settingsApi.onboarding({ step, completed }),
    onSuccess: (data, variables) => {
      qc.setQueryData(["settings"], data);
      if (variables.completed) navigate("/");
    },
  });
  if (settings.isPending) return <Busy />;
  if (settings.error instanceof ApiError && settings.error.status === 401)
    return <Navigate to="/login" replace />;
  if (!settings.data?.onboarding)
    return (
      <ReadFailure
        retry={() => void settings.refetch()}
        pending={settings.isFetching}
      />
    );
  const current = settings.data.onboarding.step;
  const index = steps.indexOf(current);
  const move = (step: Step, completed = false) =>
    transition.mutate({ step, completed });
  const next = () => move(steps[Math.min(index + 1, steps.length - 1)]);
  const content =
    current === "preferences" ? (
      <Preferences settings={settings.data} afterSave={next} />
    ) : current === "two_factor" ? (
      <TwoFactorOnboarding afterContinue={next} />
    ) : current === "review" ? (
      <Review
        settings={settings.data}
        complete={() => move("review", true)}
        pending={transition.isPending}
      />
    ) : (
      <Connection
        key={current}
        kind={current}
        settings={settings.data}
        afterSave={next}
      />
    );
  return (
    <Container size="sm" py="xl">
      <Stack gap="lg">
        {settings.error && <ReadFailure retry={() => void settings.refetch()} pending={settings.isFetching} />}
        <Group justify="space-between">
          <Title order={1}>{t("setup")}</Title>
          <LanguageButton />
        </Group>
        <Text c="dimmed">
          {index + 1} / {steps.length}
        </Text>
        {transition.isPending && <Text c="dimmed">{t("saving")}</Text>}
        {transition.error && (
          <Alert color="red">{errorMessage(transition.error, t)}</Alert>
        )}
        {content}
        {index > 0 && (
          <Button
            variant="subtle"
            onClick={() => move(steps[index - 1])}
            loading={transition.isPending}
          >
            {t("back")}
          </Button>
        )}
      </Stack>
    </Container>
  );
}

function Review({
  settings,
  complete,
  pending,
}: {
  settings: Settings;
  complete: () => void;
  pending: boolean;
}) {
  const { t } = useTranslation();
  return (
    <FormCard title={t("review")}>
      <Text>
        {t("preferences")}:{" "}
        {settings.preferences?.saved ? t("saved") : t("statusUnconfigured")}
      </Text>
      {(["postgresql", "mqtt", "smtp"] as Kind[]).map((kind) => (
        <Text key={kind}>
          {t(kind)}: {t(statusKey(settings[kind]?.status ?? "unconfigured"))}
        </Text>
      ))}
      <Button onClick={complete} loading={pending}>
        {t("finish")}
      </Button>
    </FormCard>
  );
}

function Landing() {
  const { t } = useTranslation();
  const settings = useSettings();
  if (settings.isPending) return <Busy />;
  if (settings.error || !settings.data?.onboarding)
    return (
      <ReadFailure
        retry={() => void settings.refetch()}
        pending={settings.isFetching}
      />
    );
  if (!settings.data.onboarding.completed)
    return <Navigate to="/setup" replace />;
  return (
    <Container size="sm" py="xl">
      <Stack>
        <Title order={1}>{t("setupComplete")}</Title>
        <Text c="dimmed">{t("setupLanding")}</Text>
        <Group grow>
          <Button component={Link} to="/vehicles" variant="light">{t("vehicles")}</Button>
          <Button component={Link} to="/trips" variant="light">{t("trips")}</Button>
          <Button component={Link} to="/charges" variant="light">{t("charges")}</Button>
        </Group>
        <Button component={Link} to="/settings">
          {t("editSettings")}
        </Button>
      </Stack>
    </Container>
  );
}

function SettingsPage() {
  const { t } = useTranslation();
  const settings = useSettings();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const logout = useMutation({
    mutationFn: authApi.logout,
    onSuccess: () => {
      qc.clear();
      navigate("/login");
    },
  });
  if (settings.isPending) return <Busy />;
  if (settings.error instanceof ApiError && settings.error.status === 401)
    return <Navigate to="/login" replace />;
  if (!settings.data)
    return (
      <ReadFailure
        retry={() => void settings.refetch()}
        pending={settings.isFetching}
      />
    );
  return (
    <Container size="sm" py="xl">
      <Stack gap="lg">
        {settings.error && <ReadFailure retry={() => void settings.refetch()} pending={settings.isFetching} />}
        <Title order={1}>{t("settings")}</Title>
        {logout.error && (
          <Alert color="red">{errorMessage(logout.error, t)}</Alert>
        )}
        <Preferences settings={settings.data} />
        <Connection kind="postgresql" settings={settings.data} />
        <Connection kind="mqtt" settings={settings.data} />
        <Connection kind="smtp" settings={settings.data} />
        <PasswordChange />
        <TwoFactorSettings />
        <Button variant="light" component={Link} to="/setup">
          {t("setup")}
        </Button>
        <Button
          color="red"
          variant="subtle"
          onClick={() => logout.mutate()}
          loading={logout.isPending}
        >
          {t("signOut")}
        </Button>
      </Stack>
    </Container>
  );
}

function PasswordChange() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [confirmationMismatch, setConfirmationMismatch] = useState(false);
  const [method, setMethod] = useState<FactorProof["method"]>("totp");
  const factor = useQuery({ queryKey: ["two-factor"], queryFn: authApi.twoFactorStatus });
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending) return;
    const values = new FormData(event.currentTarget);
    const formCurrent = String(values.get("current_password") ?? "");
    const formNext = String(values.get("new_password") ?? "");
    const formConfirm = String(values.get("password_confirmation") ?? "");
    const mismatch = formNext !== formConfirm;
    setConfirmationMismatch(mismatch);
    if (mismatch) return;
    const proofCode = String(values.get("password_proof_code") ?? "");
    setPending(true); setError(null);
    void authApi.changePassword({ current_password: formCurrent, new_password: formNext,
      password_confirmation: formConfirm,
      ...(factor.data?.enabled ? { proof: { method, code: proofCode } } : {}) })
      .then(() => { if (mounted.current) { qc.clear(); navigate("/login"); } })
      .catch((reason: unknown) => { if (mounted.current) setError(reason); })
      .finally(() => { if (mounted.current) setPending(false); });
  };
  return (
    <FormCard title={t("changePassword")} error={error} factorError>
      <form method="post" onSubmit={submit}>
        <Stack>
          <Text c="dimmed">{t("changePasswordNotice")}</Text>
          <PasswordInput
            id="change-password-current"
            name="current_password"
            label={t("currentPassword")}
            defaultValue={current}
            onChange={(e) => setCurrent(e.currentTarget.value)}
            autoComplete="current-password"
            required
          />
          {factor.data?.enabled && <>
            <Alert color="yellow">{t("passwordTwoFactorRequired")}</Alert>
            <FactorProofInputs id="password-proof" method={method} setMethod={setMethod} disabled={pending} />
          </>}
          <PasswordInput
            id="change-password-new"
            name="new_password"
            label={t("newPassword")}
            defaultValue={next}
            onChange={(e) => setNext(e.currentTarget.value)}
            autoComplete="new-password"
            required
          />
          <PasswordInput
            id="change-password-confirmation"
            name="password_confirmation"
            label={t("confirmPassword")}
            defaultValue={confirm}
            onChange={(e) => {
              setConfirm(e.currentTarget.value);
              setConfirmationMismatch(false);
            }}
            autoComplete="new-password"
            error={
              confirmationMismatch || (confirm && next !== confirm)
                ? t("passwordsMismatch")
                : undefined
            }
            required
          />
          <Button type="submit" loading={pending}>
            {t("updatePassword")}
          </Button>
        </Stack>
      </form>
    </FormCard>
  );
}

function ReadFailure({
  retry,
  pending,
  showLanguage = false,
}: {
  retry: () => void;
  pending: boolean;
  showLanguage?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <Container size="sm" py="xl">
      <Stack>
        <Alert color="red">{t("loadFailed")}</Alert>
        <Button onClick={retry} loading={pending}>
          {t("retry")}
        </Button>
        {showLanguage && <LanguageButton />}
      </Stack>
    </Container>
  );
}

function Protected({ children }: { children: React.ReactNode }) {
  const me = useQuery({
    queryKey: ["me"],
    queryFn: authApi.me,
  });
  if (me.isPending) return <Busy />;
  if (me.error instanceof ApiError && me.error.status === 401)
    return <Navigate to="/login" replace />;
  if (!me.data)
    return (
      <ReadFailure retry={() => void me.refetch()} pending={me.isFetching} />
    );
  // A transient refetch error must not unmount local forms or one-time secrets.
  // A confirmed 401 above still discards the protected subtree immediately.
  return <>
    {me.error && <ReadFailure retry={() => void me.refetch()} pending={me.isFetching} />}
    {children}
  </>;
}

function Shell() {
  const location = useLocation();
  const showHeader = !location.pathname.startsWith("/login");
  const { t } = useTranslation();
  const { historyPath } = useHistoryContext();
  return (
    <AppShell header={showHeader ? { height: 60 } : undefined}>
      {showHeader && (
        <AppShell.Header>
          <Container size="sm" h="100%" className="shell-header">
            <Group justify="space-between" h="100%" wrap="nowrap" gap={4}>
              <Text fw={700}>{t("appName")}</Text>
              <Group gap={2} wrap="nowrap" className="shell-header-actions">
                <Menu shadow="md" width={160} position="bottom-end">
                  <Menu.Target>
                    <Button variant="subtle" aria-label={t("navigation")}>{t("menu")}</Button>
                  </Menu.Target>
                  <Menu.Dropdown>
                    <Menu.Item component={Link} to={historyPath("/vehicles")}>{t("vehicles")}</Menu.Item>
                    <Menu.Item component={Link} to={historyPath("/trips")}>{t("trips")}</Menu.Item>
                    <Menu.Item component={Link} to={historyPath("/charges")}>{t("charges")}</Menu.Item>
                  </Menu.Dropdown>
                </Menu>
                <Button component={Link} to="/settings" variant="subtle">
                  {t("settings")}
                </Button>
                <LanguageButton />
              </Group>
            </Group>
          </Container>
        </AppShell.Header>
      )}
      <AppShell.Main>
        <Container size="sm" pt="md"><PwaStatus showInstall={showHeader} /></Container>
        <Routes>
          <Route path="/login" element={<Credentials />} />
          <Route
            path="/"
            element={
              <Protected>
                <Landing />
              </Protected>
            }
          />
          <Route
            path="/setup"
            element={
              <Protected>
                <Setup />
              </Protected>
            }
          />
          <Route
            path="/settings"
            element={
              <Protected>
                <SettingsPage />
              </Protected>
            }
          />
          <Route path="/vehicles" element={<Protected><VehiclesPage /></Protected>} />
          <Route path="/trips" element={<Protected><TripsPage /></Protected>} />
          <Route path="/trips/:id" element={<Protected><TripDetailPage /></Protected>} />
          <Route path="/charges" element={<Protected><ChargesPage /></Protected>} />
          <Route path="/charges/:id" element={<Protected><ChargeDetailPage /></Protected>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AppShell.Main>
    </AppShell>
  );
}

function App({ client = queryClient }: { client?: QueryClient }) {
  const { i18n: translator } = useTranslation();
  useOfflineVehicleDataGuard(client);
  return (
    <MantineProvider theme={appTheme} defaultColorScheme="auto">
      <DatesProvider settings={{ firstDayOfWeek: 1, locale: translator.language.startsWith("zh") ? "zh-cn" : "en" }}>
      <QueryClientProvider client={client}>
        <BrowserRouter>
          <HistoryContextProvider><Shell /></HistoryContextProvider>
        </BrowserRouter>
      </QueryClientProvider>
      </DatesProvider>
    </MantineProvider>
  );
}
const root = document.getElementById("root");
if (root) createRoot(root).render(<App />);
registerPwa();
export default App;
