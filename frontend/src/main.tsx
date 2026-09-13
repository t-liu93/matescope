import "@mantine/core/styles.css";
import "./styles.css";
import "./i18n";

import {
  AppShell,
  Badge,
  Button,
  Container,
  Group,
  MantineProvider,
  Paper,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { api } from "./api/client";
import i18n from "./i18n";

const queryClient = new QueryClient();

function Home() {
  const { t } = useTranslation();
  const health = useQuery({
    queryKey: ["health"],
    queryFn: async () => {
      const response = await api.GET("/api/v1/health");
      if (response.error) {
        throw new Error("API error");
      }
      return response.data;
    },
  });

  return (
    <Container size="md" py="xl">
      <Stack gap="xl">
        <div>
          <Badge variant="light">M0-T01</Badge>
          <Title order={1} mt="sm">{t("appName")}</Title>
          <Text size="lg" c="dimmed">{t("tagline")}</Text>
        </div>
        <Paper withBorder radius="lg" p="xl">
          <Stack gap="md">
            <Title order={2}>{t("shellReady")}</Title>
            <Group justify="space-between">
              <Text>{t("apiStatus")}</Text>
              <Badge color={health.isSuccess ? "teal" : "gray"}>
                {health.isSuccess ? t("apiOnline") : t("apiOffline")}
              </Badge>
            </Group>
            <Text c="dimmed">{t("next")}</Text>
            <Button component={Link} to="/status" variant="light">{t("apiStatus")}</Button>
          </Stack>
        </Paper>
      </Stack>
    </Container>
  );
}

function Status() {
  const { t } = useTranslation();

  return (
    <Container size="md" py="xl">
      <Title order={1}>{t("apiStatus")}</Title>
      <Button mt="lg" component={Link} to="/" variant="subtle">{t("appName")}</Button>
    </Container>
  );
}

function Shell() {
  const { t } = useTranslation();

  return (
    <AppShell header={{ height: 64 }}>
      <AppShell.Header>
        <Container size="md" h="100%">
          <Group justify="space-between" h="100%">
            <Text fw={700}>{t("appName")}</Text>
            <Button
              variant="subtle"
              onClick={() => void i18n.changeLanguage(i18n.language === "en" ? "zh" : "en")}
            >
              {t("language")}
            </Button>
          </Group>
        </Container>
      </AppShell.Header>
      <AppShell.Main>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/status" element={<Status />} />
        </Routes>
      </AppShell.Main>
    </AppShell>
  );
}

export function App() {
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

const rootElement = document.getElementById("root");

if (rootElement) {
  createRoot(rootElement).render(<App />);
}

export default App;
