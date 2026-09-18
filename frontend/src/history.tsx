import {
  Alert,
  Button,
  Card,
  Container,
  Group,
  Select,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { DatePickerInput } from "@mantine/dates";
import { useMediaQuery } from "@mantine/hooks";
import { useQuery } from "@tanstack/react-query";
import { Component, lazy, Suspense, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { ApiError, historyApi, settingsApi, type HistoryWindow } from "./api/client";
import type { components } from "./api/schema";
import { addCalendarDays, defaultWindow, groupTrajectory, localDateIso, windowToCalendarRange, type CalendarRange } from "./history-utils";
import type { HistoryWindowPreset } from "./api/client";
import i18n from "./i18n";
import { useOnlineStatus } from "./pwa";
import { HistorySelectionGuard, useHistoryContext } from "./history-context";

type Trip = components["schemas"]["Trip"];
type Charge = components["schemas"]["Charge"];
type HistoryPage = components["schemas"]["TripPage"] | components["schemas"]["ChargePage"];

const TrajectoryMap = lazy(() => import("./trajectory-map"));

function useHistorySettings() {
  return useQuery({
    queryKey: ["settings"],
    staleTime: Infinity,
    retryOnMount: false,
    queryFn: async () => {
      const settings = await settingsApi.get();
      if (settings.preferences?.saved) await i18n.changeLanguage(settings.preferences.language);
      return settings;
    },
  });
}

class MapBoundary extends Component<{ children: React.ReactNode; failed: () => void }> {
  componentDidCatch() { this.props.failed(); }
  render() { return this.props.children; }
}

function formatDate(value: string | null, timezone: string) {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: timezone,
  }).format(new Date(value));
}

function value(value: number | null, unit: string) {
  return value === null ? "—" : `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })} ${unit}`;
}

function HistoryFailure({ retry }: { retry: () => void }) {
  const { t } = useTranslation();
  return (
    <Alert color="red">
      <Stack gap="xs">
        <Text>{t("historyLoadFailed")}</Text>
        <Button variant="light" onClick={retry}>{t("retry")}</Button>
      </Stack>
    </Alert>
  );
}

function OfflineVehicleData() {
  const { t } = useTranslation();
  return <Container py="xl"><Alert color="yellow">{t("offlineVehicleData")}</Alert></Container>;
}

function HistoryFilters({
  window,
  clear,
  timezone,
}: {
  window: HistoryWindow;
  clear: () => void;
  timezone: string;
}) {
  const { t } = useTranslation();
  const { vehicle, vehicles, preset, setVehicleId, setPreset, cancelPreset } = useHistoryContext();
  const options = vehicles.map((available) => ({
    value: String(available.id), label: available.name || available.model || `${t("vehicle")} ${available.id}`,
  }));
  const [message, setMessage] = useState<string | null>(null);
  const submission = useRef(0);
  const [presetDraft, setPresetDraft] = useState<HistoryWindowPreset>(preset);
  const [range, setRange] = useState<CalendarRange>(() => windowToCalendarRange(window, timezone));
  const mobile = useMediaQuery("(max-width: 767px)");
  useEffect(() => { setPresetDraft(preset); setRange(windowToCalendarRange(window, timezone)); }, [preset, window, timezone]);
  const choosePreset = (preset: typeof presetDraft) => {
    setMessage(null);
    setPresetDraft(preset);
    if (preset === "all_history") return;
    const today = localDateIso(new Date(), timezone);
    let start = today;
    if (preset === "last_7_days") start = addCalendarDays(today, -6);
    if (preset === "last_30_days") start = addCalendarDays(today, -29);
    if (preset === "this_month") start = `${today.slice(0, 8)}01`;
    if (preset === "this_year") start = `${today.slice(0, 4)}-01-01`;
    setRange([start, today]);
  };
  const submit = async () => {
    const submissionToken = ++submission.current;
    if (presetDraft === "all_history") {
      setMessage(null);
      try {
        await setPreset(presetDraft);
      } catch {
        if (submission.current === submissionToken) setMessage("historyLoadFailed");
      }
      return;
    }
    if (!range[0] || !range[1]) { setMessage("historyIncompleteWindow"); return; }
    setMessage(null);
    try {
      await setPreset(presetDraft, presetDraft === "custom" ? [range[0], range[1]] : undefined);
    } catch {
      if (submission.current === submissionToken) setMessage("historyLoadFailed");
    }
  };
  return (
    <Card withBorder radius="md">
      <Stack gap="sm">
        <Text fw={600}>{t("filters")}</Text>
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          <Select
            label={t("vehicle")}
            value={vehicle ? String(vehicle.id) : null}
            data={options}
            onChange={(id) => id && setVehicleId(Number(id))}
          />
          <DatePickerInput type="range" label={t("dateRange")} value={range} onChange={(value) => { setPresetDraft("custom"); setRange(value); }} dropdownType={mobile ? "modal" : "popover"} firstDayOfWeek={1} maxDate={localDateIso(new Date(), timezone)} valueFormat="YYYY-MM-DD" ariaLabels={{ previousYear: t("previousYear"), nextYear: t("nextYear"), previousMonth: t("previousMonth"), nextMonth: t("nextMonth"), previousDecade: t("previousDecade"), nextDecade: t("nextDecade"), yearLevelControl: t("changeYear"), monthLevelControl: t("changeMonth") }} />
        </SimpleGrid>
        <Group gap="xs" wrap="wrap">
          {(["today", "last_7_days", "last_30_days", "this_month", "this_year", "all_history", "custom"] as const).map((preset) => <Button key={preset} size="compact-sm" variant={presetDraft === preset ? "filled" : "light"} onClick={() => choosePreset(preset)}>{t(`preset_${preset}`)}</Button>)}
        </Group>
        <Text size="sm" c="dimmed">{t("calendarWindowHelp", { timezone })}</Text>
        {message && <Alert color="red">{t(message)}</Alert>}
        <Group>
          <Button onClick={() => void submit()}>{t("apply")}</Button>
          <Button variant="subtle" onClick={() => { submission.current += 1; cancelPreset(); setMessage(null); setPresetDraft(preset); setRange(windowToCalendarRange(window, timezone)); clear(); }}>
            {t("cancel")}
          </Button>
        </Group>
      </Stack>
    </Card>
  );
}

function HistoryList({ kind }: { kind: "trips" | "charges" }) {
  const { t } = useTranslation();
  const { vehicle, window: scopedWindow, emptyWindow, historyPath } = useHistoryContext();
  const [draft, setDraft] = useState(defaultWindow);
  const [cursors, setCursors] = useState<string[]>([]);
  const scope = `${vehicle?.id ?? ""}:${scopedWindow?.start ?? ""}:${scopedWindow?.end ?? ""}`;
  const [cursorScope, setCursorScope] = useState(scope);
  const activeCursors = cursorScope === scope ? cursors : [];
  const cursor = activeCursors.at(-1);
  const preferences = useHistorySettings();
  const online = useOnlineStatus();
  useEffect(() => { if (scopedWindow) setDraft(scopedWindow); }, [scopedWindow]);
  useEffect(() => { setCursors([]); setCursorScope(scope); }, [scope]);
  const query = useQuery<HistoryPage>({
    queryKey: [kind, scopedWindow, cursor],
    queryFn: ({ signal }) => kind === "trips"
      ? historyApi.trips({ ...scopedWindow!, vehicleId: vehicle!.id, cursor }, { signal })
      : historyApi.charges({ ...scopedWindow!, vehicleId: vehicle!.id, cursor }, { signal }),
    enabled: online && !emptyWindow && Boolean(preferences.data?.preferences?.saved) && Boolean(vehicle && scopedWindow),
  });
  const clear = () => {
    if (scopedWindow) setDraft(scopedWindow);
  };
  const page = query.data;
  if (!online) return <OfflineVehicleData />;
  if (preferences.isPending) return <Container py="xl"><Text>{t("loading")}</Text></Container>;
  if (preferences.error || !preferences.data?.preferences?.saved)
    return <Container py="xl"><HistoryFailure retry={() => void preferences.refetch()} /></Container>;
  const timezone = preferences.data.preferences.timezone;
  return (
    <Container size="md" py="xl">
      <Stack gap="lg">
        <Title order={1}>{t(kind)}</Title>
        <Text size="sm" c="dimmed">{t("timesShownIn", { timezone })}</Text>
        <HistoryFilters window={draft} clear={clear} timezone={timezone} />
        {query.isPending && <Text>{t("loading")}</Text>}
        {query.error && <HistoryFailure retry={() => void query.refetch()} />}
        {(emptyWindow || page?.items.length === 0) && <Alert>{t("noHistory")}</Alert>}
        {page?.items.map((item) => (
          <Card key={item.id} withBorder radius="md">
            <Stack gap="xs">
              <Group justify="space-between" align="start">
                <Text fw={600}>{formatDate(item.start, timezone)}</Text>
                <Text c="dimmed">{t("vehicle")} {item.vehicle_id}</Text>
              </Group>
              <Text>{item.end ? `${formatDate(item.start, timezone)} – ${formatDate(item.end, timezone)}` : t("unfinished")}</Text>
              <Group gap="md">
                <Text>{value(item.duration_min, "min")}</Text>
                {kind === "trips" ? <><Text>{value((item as Trip).distance_km, "km")}</Text><Text>{value((item as Trip).speed_max_kmh, "km/h")}</Text></> : <Text>{value((item as Charge).energy_added_kwh, "kWh")}</Text>}
              </Group>
              <Button component={Link} variant="light" to={historyPath(`/${kind}/${item.id}`)}>{t("viewDetails")}</Button>
            </Stack>
          </Card>
        ))}
        <Group>
          {activeCursors.length > 0 && <Button variant="light" onClick={() => { setCursorScope(scope); setCursors((value) => [...value].slice(0, -1)); }}>{t("previousPage")}</Button>}
          {page?.next_cursor && <Button variant="light" onClick={() => { setCursorScope(scope); setCursors((value) => [...value, page.next_cursor!]); }}>{t("nextPage")}</Button>}
        </Group>
      </Stack>
    </Container>
  );
}

function DetailValues({ item, kind, timezone }: { item: Trip | Charge; kind: "trips" | "charges"; timezone: string }) {
  const { t } = useTranslation();
  return <SimpleGrid cols={{ base: 1, sm: 2 }}>
    <Text>{t("start")}: {formatDate(item.start, timezone)}</Text>
    <Text>{t("end")}: {item.end ? formatDate(item.end, timezone) : t("unfinished")}</Text>
    <Text>{t("duration")}: {value(item.duration_min, "min")}</Text>
    {kind === "trips" ? <><Text>{t("distance")}: {value((item as Trip).distance_km, "km")}</Text><Text>{t("maxSpeed")}: {value((item as Trip).speed_max_kmh, "km/h")}</Text></> : <Text>{t("energyAdded")}: {value((item as Charge).energy_added_kwh, "kWh")}</Text>}
  </SimpleGrid>;
}

function HistoryDetail({ kind }: { kind: "trips" | "charges" }) {
  const { t } = useTranslation();
  const params = useParams();
  const identifier = Number(params.id);
  const settings = useHistorySettings();
  const online = useOnlineStatus();
  const { setVehicleId, historyPath } = useHistoryContext();
  const detail = useQuery<Trip | Charge>({
    queryKey: [kind, identifier],
    queryFn: ({ signal }) => kind === "trips" ? historyApi.trip(identifier, { signal }) : historyApi.charge(identifier, { signal }),
    enabled: online && Number.isInteger(identifier) && identifier > 0 && Boolean(settings.data?.preferences?.saved),
    staleTime: Infinity,
  });
  const trajectory = useQuery({ queryKey: ["trajectory", identifier], queryFn: ({ signal }) => historyApi.trajectory(identifier, { signal }), enabled: online && kind === "trips" && detail.isSuccess && Boolean(settings.data?.preferences?.saved) });
  const [mapModuleFailed, setMapModuleFailed] = useState(false);
  const [tileFailed, setTileFailed] = useState(false);
  useEffect(() => {
    // The owner is discovered after entering a direct detail URL. Replacing
    // that URL preserves the list entry that led here for browser Back.
    if (detail.data?.vehicle_id !== undefined) setVehicleId(detail.data.vehicle_id, { replace: true });
  }, [detail.data?.vehicle_id, setVehicleId]);
  if (!Number.isInteger(identifier) || identifier <= 0) return <Container py="xl"><Alert color="red">{t("historyNotFound")}</Alert></Container>;
  if (!online) return <OfflineVehicleData />;
  if (settings.isPending) return <Container py="xl"><Text>{t("loading")}</Text></Container>;
  if (settings.error || !settings.data?.preferences?.saved)
    return <Container py="xl"><HistoryFailure retry={() => void settings.refetch()} /></Container>;
  if (detail.isPending) return <Container py="xl"><Text>{t("loading")}</Text></Container>;
  if (detail.error instanceof ApiError && detail.error.status === 404)
    return <Container py="xl"><Alert color="red">{t("historyNotFound")}</Alert></Container>;
  if (detail.error || !detail.data) return <Container py="xl"><HistoryFailure retry={() => void detail.refetch()} /></Container>;
  const item = detail.data;
  const timezone = settings.data.preferences.timezone;
  return <Container size="md" py="xl"><Stack gap="lg">
    <Button component={Link} variant="subtle" to={historyPath(`/${kind}`)}>{kind === "trips" ? t("backToTrips") : t("backToCharges")}</Button>
    <Title order={1}>{kind === "trips" ? t("trip") : t("charge")}</Title>
    <Text size="sm" c="dimmed">{t("timesShownIn", { timezone })}</Text>
    <Card withBorder radius="md"><DetailValues item={item} kind={kind} timezone={timezone} /></Card>
    {kind === "trips" && <Card key={identifier} withBorder radius="md"><Stack><Title order={2}>{t("route")}</Title>
      {trajectory.isPending && <Text>{t("loadingMap")}</Text>}
      {(trajectory.error || mapModuleFailed || tileFailed) && <Alert color="yellow">{t("mapUnavailable")}</Alert>}
      {trajectory.data?.points.length === 0 && <Alert>{t("noTrajectory")}</Alert>}
      {trajectory.data && trajectory.data.points.length > 0 && !mapModuleFailed && <MapBoundary failed={() => setMapModuleFailed(true)}><Suspense fallback={<Text>{t("loadingMap")}</Text>}><TrajectoryMap points={groupTrajectory(trajectory.data.points)} tileUrl={settings.data.preferences.tile_url} onTileError={() => setTileFailed(true)} /></Suspense></MapBoundary>}
      {trajectory.data?.simplified && <Text size="sm" c="dimmed">{t("trajectorySimplified", { count: trajectory.data.total_points })}</Text>}
    </Stack></Card>}
  </Stack></Container>;
}

export function VehiclesPage() {
  const { t } = useTranslation();
  const settings = useHistorySettings();
  const online = useOnlineStatus();
  const vehicles = useQuery({
    queryKey: ["vehicles"],
    queryFn: ({ signal }) => historyApi.vehicles({ signal }),
    enabled: online && Boolean(settings.data?.preferences?.saved),
  });
  if (!online) return <OfflineVehicleData />;
  if (settings.isPending) return <Container py="xl"><Text>{t("loading")}</Text></Container>;
  if (settings.error || !settings.data?.preferences?.saved)
    return <Container py="xl"><HistoryFailure retry={() => void settings.refetch()} /></Container>;
  return <Container size="md" py="xl"><Stack gap="lg"><Title order={1}>{t("vehicles")}</Title>
    {vehicles.isPending && <Text>{t("loading")}</Text>}
    {vehicles.error && <HistoryFailure retry={() => void vehicles.refetch()} />}
    {vehicles.data?.items.length === 0 && <Alert>{t("noVehicles")}</Alert>}
    {vehicles.data?.items.map((vehicle) => <Card key={vehicle.id} withBorder radius="md"><Text fw={600}>{vehicle.name || t("unnamedVehicle")}</Text><Text c="dimmed">{vehicle.model || t("notAvailable")} · #{vehicle.id}</Text></Card>)}
  </Stack></Container>;
}

export const TripsPage = () => <HistorySelectionGuard><HistoryList kind="trips" /></HistorySelectionGuard>;
export const ChargesPage = () => <HistorySelectionGuard><HistoryList kind="charges" /></HistorySelectionGuard>;
export const TripDetailPage = () => {
  const { id } = useParams();
  return <HistoryDetail key={id} kind="trips" />;
};
export const ChargeDetailPage = () => {
  const { id } = useParams();
  return <HistoryDetail key={id} kind="charges" />;
};
