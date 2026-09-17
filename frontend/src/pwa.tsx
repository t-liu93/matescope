/* eslint-disable react-refresh/only-export-components */
import { Alert, Button, Group, Stack, Text } from "@mantine/core";
import type { QueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

declare global {
  interface WindowEventMap { beforeinstallprompt: BeforeInstallPromptEvent; }
}

const vehicleQueryKeys = new Set(["vehicles", "trips", "charges", "trajectory"]);

export function isVehicleDataQuery(queryKey: readonly unknown[]) {
  return vehicleQueryKeys.has(String(queryKey[0]));
}

export function clearVehicleData(client: QueryClient) {
  const filters = { predicate: (query: { queryKey: readonly unknown[] }) => isVehicleDataQuery(query.queryKey) };
  void client.cancelQueries(filters);
  client.removeQueries(filters);
}

export function useOnlineStatus() {
  const [online, setOnline] = useState(() => navigator.onLine);
  useEffect(() => {
    const goOnline = () => setOnline(true);
    const goOffline = () => setOnline(false);
    window.addEventListener("online", goOnline);
    window.addEventListener("offline", goOffline);
    return () => {
      window.removeEventListener("online", goOnline);
      window.removeEventListener("offline", goOffline);
    };
  }, []);
  return online;
}

/** Vehicle records are intentionally memory-only while the browser is online. */
export function useOfflineVehicleDataGuard(client: QueryClient) {
  const online = useOnlineStatus();
  useEffect(() => {
    if (online) return;
    clearVehicleData(client);
  }, [client, online]);
  return online;
}

export function PwaStatus({ showInstall = true }: { showInstall?: boolean }) {
  const { t } = useTranslation();
  const online = useOnlineStatus();
  const [installPrompt, setInstallPrompt] = useState<BeforeInstallPromptEvent | null>(null);
  const [installed, setInstalled] = useState(() =>
    window.matchMedia("(display-mode: standalone)").matches ||
    Boolean((navigator as Navigator & { standalone?: boolean }).standalone),
  );

  useEffect(() => {
    const installedHandler = () => { setInstalled(true); setInstallPrompt(null); };
    const promptHandler = (event: BeforeInstallPromptEvent) => { event.preventDefault(); setInstallPrompt(event); };
    window.addEventListener("appinstalled", installedHandler);
    window.addEventListener("beforeinstallprompt", promptHandler);
    return () => {
      window.removeEventListener("appinstalled", installedHandler);
      window.removeEventListener("beforeinstallprompt", promptHandler);
    };
  }, []);

  const install = async () => {
    if (!installPrompt) return;
    await installPrompt.prompt();
    const choice = await installPrompt.userChoice;
    if (choice.outcome === "accepted") setInstalled(true);
    setInstallPrompt(null);
  };

  return (
    <Stack gap="xs" mb="md">
      {!online && <Alert color="yellow" title={t("offlineTitle")}>
        <Text size="sm">{t("offlineMessage")}</Text>
        <Button mt="sm" size="xs" variant="light" onClick={() => window.location.reload()}>{t("retry")}</Button>
      </Alert>}
      {showInstall && !installed && <Alert color="blue" role="status" title={t("installTitle")}>
        <Group justify="space-between" align="center" wrap="wrap">
          <Text size="sm">{installPrompt ? t("installMessage") : t("installMenuMessage")}</Text>
          {installPrompt && <Button size="xs" onClick={() => void install()}>{t("install")}</Button>}
        </Group>
      </Alert>}
    </Stack>
  );
}

export function registerPwa() {
  if (!import.meta.env.PROD || !window.isSecureContext || !("serviceWorker" in navigator)) return;
  window.addEventListener("load", () => {
    void navigator.serviceWorker.register("/sw.js", { scope: "/", updateViaCache: "none" }).catch(() => undefined);
  });
}
