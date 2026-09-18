/* eslint-disable react-refresh/only-export-components */
import { Alert, Button, Container, Select, Stack } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { historyApi, settingsApi, type HistoryWindow, type HistoryWindowPreset } from "./api/client";
import type { components } from "./api/schema";
import { parseUtcIso } from "./history-utils";
import { clearVehicleData, useOnlineStatus } from "./pwa";
import i18n from "./i18n";

type Vehicle = components["schemas"]["Vehicle"];
type Selection = {
  vehicle: Vehicle | null;
  vehicles: Vehicle[];
  window: HistoryWindow | null;
  unavailableUrlVehicle: boolean;
  selectionLoading: boolean;
  selectionError: boolean;
  setVehicleId: (id: number, options?: { replace?: boolean }) => void;
  setWindow: (window: HistoryWindow) => void;
  historyPath: (pathname: string) => string;
};

const HistoryContext = createContext<Selection | null>(null);

function urlWindow(search: URLSearchParams): HistoryWindow | null {
  const start = search.get("start");
  const end = search.get("end");
  return start && end && parseUtcIso(start) && parseUtcIso(end) && new Date(end) > new Date(start)
    ? { start, end }
    : null;
}

export function HistoryContextProvider({ children }: { children: ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const client = useQueryClient();
  const online = useOnlineStatus();
  const [rememberedVehicleId, setRememberedVehicleId] = useState<number | null>(null);
  const search = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const rawUrlVehicle = search.get("vehicle");
  const historyRoute = /^\/(vehicles|trips|charges)(?:\/|$)/.test(location.pathname);
  const settings = useQuery({ queryKey: ["settings"], queryFn: settingsApi.get, enabled: historyRoute, staleTime: Infinity });
  const explicitVehicleId = rawUrlVehicle && /^\d+$/.test(rawUrlVehicle) ? Number(rawUrlVehicle) : null;
  const hasExplicitVehicle = rawUrlVehicle !== null;
  const preset = (search.get("preset") === "all_history" ? "all_history" : "last_30_days") as HistoryWindowPreset;
  const vehicles = useQuery({
    queryKey: ["vehicles"], queryFn: ({ signal }) => historyApi.vehicles({ signal }), enabled: online && historyRoute && Boolean(settings.data?.preferences?.saved),
  });
  const items = useMemo(() => vehicles.data?.items ?? [], [vehicles.data?.items]);
  const lowestVehicleId = useMemo(() => items.reduce<number | null>((lowest, vehicle) => lowest === null || vehicle.id < lowest ? vehicle.id : lowest, null), [items]);
  const selectedId = explicitVehicleId ?? rememberedVehicleId ?? lowestVehicleId;
  const selectedVehicle = items.find((vehicle) => vehicle.id === selectedId) ?? null;
  const unavailableUrlVehicle = hasExplicitVehicle && (!explicitVehicleId || !selectedVehicle) && !vehicles.isPending;
  const suppliedWindow = useMemo(() => urlWindow(search), [search]);
  const resolve = useQuery({
    queryKey: ["history-window", selectedVehicle?.id, preset],
    queryFn: ({ signal }) => historyApi.historyWindow(selectedVehicle!.id, preset, { signal }),
    enabled: online && historyRoute && Boolean(settings.data?.preferences?.saved) && Boolean(selectedVehicle) && !suppliedWindow,
  });
  useEffect(() => { if (settings.data?.preferences?.saved) void i18n.changeLanguage(settings.data.preferences.language); }, [settings.data]);
  const resolvedWindow = useMemo(() => suppliedWindow ?? (resolve.data?.start && resolve.data.end
    ? { start: resolve.data.start, end: resolve.data.end } : null), [resolve.data?.end, resolve.data?.start, suppliedWindow]);

  useEffect(() => {
    if (selectedVehicle) setRememberedVehicleId(selectedVehicle.id);
  }, [selectedVehicle]);
  useEffect(() => {
    if (!selectedVehicle || !resolvedWindow || unavailableUrlVehicle) return;
    const next = new URLSearchParams(location.search);
    next.set("vehicle", String(selectedVehicle.id));
    next.set("start", resolvedWindow.start);
    next.set("end", resolvedWindow.end);
    const target = `${location.pathname}?${next.toString()}`;
    if (target !== `${location.pathname}${location.search}`) navigate(target, { replace: true });
  }, [location.pathname, location.search, navigate, resolvedWindow, selectedVehicle, unavailableUrlVehicle]);

  const setVehicleId = useCallback((id: number, options?: { replace?: boolean }) => {
    if (id === selectedVehicle?.id && search.get("vehicle") === String(id)) return;
    // Direct detail links often establish the same default car after their
    // request starts. Only a real car change may cancel vehicle queries.
    if (selectedVehicle && id !== selectedVehicle.id) clearVehicleData(client);
    setRememberedVehicleId(id);
    const next = new URLSearchParams(location.search);
    next.set("vehicle", String(id)); next.delete("cursor");
    // Fixed UTC windows are a navigation contract. All-history instead belongs
    // to each vehicle's earliest record and must be resolved again after a switch.
    if (next.get("preset") === "all_history") { next.delete("start"); next.delete("end"); }
    navigate(`${location.pathname}?${next.toString()}`, { replace: options?.replace });
  }, [client, location.search, location.pathname, navigate, search, selectedVehicle]);
  const setWindow = useCallback((window: HistoryWindow) => {
    if (!selectedVehicle) return;
    clearVehicleData(client);
    const next = new URLSearchParams(location.search);
    next.set("vehicle", String(selectedVehicle.id)); next.set("start", window.start); next.set("end", window.end); next.delete("cursor");
    navigate(`${location.pathname}?${next.toString()}`);
  }, [client, location.search, location.pathname, navigate, selectedVehicle]);
  const historyPath = useCallback((pathname: string) => {
    if (!selectedVehicle || !resolvedWindow) return pathname;
    const scoped = new URLSearchParams({ vehicle: String(selectedVehicle.id), start: resolvedWindow.start, end: resolvedWindow.end });
    if (search.get("preset")) scoped.set("preset", search.get("preset")!);
    return `${pathname}?${scoped.toString()}`;
  }, [resolvedWindow, search, selectedVehicle]);
  const value = useMemo(() => ({ vehicle: selectedVehicle, vehicles: items, window: resolvedWindow, unavailableUrlVehicle, selectionLoading: settings.isPending || vehicles.isPending, selectionError: Boolean(settings.error || vehicles.error), setVehicleId, setWindow, historyPath }), [selectedVehicle, items, resolvedWindow, unavailableUrlVehicle, settings.isPending, vehicles.isPending, settings.error, vehicles.error, setVehicleId, setWindow, historyPath]);
  return <HistoryContext.Provider value={value}>{children}</HistoryContext.Provider>;
}

export function useHistoryContext() {
  const value = useContext(HistoryContext);
  if (!value) throw new Error("HistoryContextProvider is required");
  return value;
}

export function HistorySelectionGuard({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const { vehicle, vehicles, unavailableUrlVehicle, selectionLoading, selectionError, setVehicleId } = useHistoryContext();
  if (selectionLoading || selectionError) return <>{children}</>;
  if (unavailableUrlVehicle) return <Container py="xl"><Stack><Alert color="yellow">{t("unavailableVehicle")}</Alert><Select aria-label={t("vehicle")} placeholder={t("vehicle")} data={vehicles.map((available) => ({ value: String(available.id), label: available.name || available.model || `${t("vehicle")} ${available.id}` }))} onChange={(id) => id && setVehicleId(Number(id))} /></Stack></Container>;
  if (!vehicle) return <Container py="xl"><Stack><Alert>{t("noVehicles")}</Alert><Button component={Link} to="/settings">{t("settings")}</Button></Stack></Container>;
  return <>{children}</>;
}
