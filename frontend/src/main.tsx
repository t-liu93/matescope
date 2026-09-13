import "@mantine/core/styles.css";
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
  Paper,
  PasswordInput,
  Select,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import {
  QueryClient,
  QueryClientProvider,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { createRoot } from "react-dom/client";
import { useState } from "react";
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
import type { components } from "./api/schema";
import i18n from "./i18n";

type Settings = components["schemas"]["SettingsResponse"];
type Step = components["schemas"]["Onboarding"]["step"];
type PasswordAction = components["schemas"]["PasswordChange"]["action"];
type PostgreSQLInput = components["schemas"]["PostgreSQLInput"];
type MqttInput = components["schemas"]["MQTTInput"];
type SmtpInput = components["schemas"]["SMTPInput"];
const steps: Step[] = ["preferences", "postgresql", "mqtt", "smtp", "review"];
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
  const create = useMutation({
    mutationFn: () =>
      authApi.createAdministrator({
        username,
        password,
        password_confirmation: confirmation,
      }),
    onSuccess: (data) => {
      setCsrf(data.csrf_token);
      void client.invalidateQueries({ queryKey: ["setup"] });
      navigate("/setup", { replace: true });
    },
  });
  const login = useMutation({
    mutationFn: () => authApi.login({ username, password }),
    onSuccess: (data) => {
      setCsrf(data.csrf_token);
      navigate("/", { replace: true });
    },
  });
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
  const submit = () => {
    if (creating && password !== confirmation) return;
    (creating ? create : login).mutate();
  };
  const error = create.error ?? login.error;
  return (
    <Container size="xs" py="xl">
      <Stack gap="lg">
        <Group justify="space-between">
          <Title order={1}>{t("appName")}</Title>
          <LanguageButton />
        </Group>
        <Paper withBorder radius="lg" p="xl">
          <Stack>
            <Title order={2}>
              {creating ? t("createTitle") : t("loginTitle")}
            </Title>
            {creating && <Text c="dimmed">{t("createIntro")}</Text>}
            <TextInput
              label={t("username")}
              value={username}
              onChange={(e) => setUsername(e.currentTarget.value)}
              autoComplete="username"
              required
            />
            <PasswordInput
              label={t("password")}
              value={password}
              onChange={(e) => setPassword(e.currentTarget.value)}
              autoComplete={creating ? "new-password" : "current-password"}
              required
            />
            {creating && (
              <>
                <PasswordInput
                  label={t("confirmPassword")}
                  value={confirmation}
                  onChange={(e) => setConfirmation(e.currentTarget.value)}
                  autoComplete="new-password"
                  error={
                    confirmation && password !== confirmation
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
            {error && <Alert color="red">{errorMessage(error, t)}</Alert>}
            <Button
              onClick={submit}
              loading={create.isPending || login.isPending}
              disabled={
                !username ||
                !password ||
                (creating && (!confirmation || password !== confirmation))
              }
            >
              {creating ? t("create") : t("signIn")}
            </Button>
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
  const save = useMutation({
    mutationFn: () =>
      saveConnection(
        kind,
        input(false) as PostgreSQLInput | MqttInput | SmtpInput,
      ),
    onSuccess: (result) => {
      queryClient.setQueryData(["settings"], result);
      setPassword("");
      setAction("retain");
      afterSave?.();
    },
  });
  const skip = useMutation({
    mutationFn: () =>
      saveConnection(
        kind,
        input(true) as PostgreSQLInput | MqttInput | SmtpInput,
      ),
    onSuccess: (result) => {
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
      afterSave?.();
    },
  });
  const set = (key: string, value: string | boolean) =>
    setExtra((old) => ({ ...old, [key]: value }));
  const heading = t(kind);
  return (
    <FormCard title={heading} error={save.error ?? skip.error}>
      <Group justify="space-between">
        <Badge color={data.status === "unverified" ? "yellow" : "gray"}>
          {t(statusKey(data.status))}
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
    </FormCard>
  );
}

function FormCard({
  title,
  children,
  error,
}: {
  title: string;
  children: React.ReactNode;
  error?: unknown;
}) {
  const { t } = useTranslation();
  return (
    <Paper component="section" aria-label={title} withBorder radius="lg" p="xl">
      <Stack>
        <Title order={2}>{title}</Title>
        {Boolean(error) && <Alert color="red">{errorMessage(error, t)}</Alert>}
        {children}
      </Stack>
    </Paper>
  );
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
  if (settings.error || !settings.data?.onboarding)
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
  if (settings.error || !settings.data)
    return (
      <ReadFailure
        retry={() => void settings.refetch()}
        pending={settings.isFetching}
      />
    );
  return (
    <Container size="sm" py="xl">
      <Stack gap="lg">
        <Title order={1}>{t("settings")}</Title>
        {logout.error && (
          <Alert color="red">{errorMessage(logout.error, t)}</Alert>
        )}
        <Preferences settings={settings.data} />
        <Connection kind="postgresql" settings={settings.data} />
        <Connection kind="mqtt" settings={settings.data} />
        <Connection kind="smtp" settings={settings.data} />
        <PasswordChange />
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
  const mutation = useMutation({
    mutationFn: () =>
      authApi.changePassword({
        current_password: current,
        new_password: next,
        password_confirmation: confirm,
      }),
    onSuccess: () => {
      qc.clear();
      navigate("/login");
    },
  });
  return (
    <FormCard title={t("changePassword")} error={mutation.error}>
      <Text c="dimmed">{t("changePasswordNotice")}</Text>
      <PasswordInput
        label={t("currentPassword")}
        value={current}
        onChange={(e) => setCurrent(e.currentTarget.value)}
      />
      <PasswordInput
        label={t("newPassword")}
        value={next}
        onChange={(e) => setNext(e.currentTarget.value)}
      />
      <PasswordInput
        label={t("confirmPassword")}
        value={confirm}
        onChange={(e) => setConfirm(e.currentTarget.value)}
        error={confirm && next !== confirm ? t("passwordsMismatch") : undefined}
      />
      <Button
        disabled={!current || !next || next !== confirm}
        loading={mutation.isPending}
        onClick={() => mutation.mutate()}
      >
        {t("updatePassword")}
      </Button>
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
  if (me.error || !me.data)
    return (
      <ReadFailure retry={() => void me.refetch()} pending={me.isFetching} />
    );
  return <>{children}</>;
}

function Shell() {
  const location = useLocation();
  const showHeader = !location.pathname.startsWith("/login");
  const { t } = useTranslation();
  return (
    <AppShell header={showHeader ? { height: 60 } : undefined}>
      {showHeader && (
        <AppShell.Header>
          <Container size="sm" h="100%">
            <Group justify="space-between" h="100%">
              <Text fw={700}>{t("appName")}</Text>
              <Group gap="xs">
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
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AppShell.Main>
    </AppShell>
  );
}

function App() {
  return (
    <MantineProvider defaultColorScheme="auto">
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Shell />
        </BrowserRouter>
      </QueryClientProvider>
    </MantineProvider>
  );
}
const root = document.getElementById("root");
if (root) createRoot(root).render(<App />);
export default App;
