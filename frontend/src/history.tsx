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
  TextInput,
  Title,
} from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { Component, lazy, Suspense, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { ApiError, historyApi, settingsApi, type HistoryWindow } from "./api/client";
import type { components } from "./api/schema";
import { defaultWindow, groupTrajectory, validateWindow } from "./history-utils";
import i18n from "./i18n";

type Trip = components["schemas"]["Trip"];
type Charge = components["schemas"]["Charge"];
type HistoryPage = components["schemas"]["TripPage"] | components["schemas"]["ChargePage"];

const TrajectoryMap = lazy(() => import("./trajectory-map"));

function useHistorySettings() {
  return useQuery({
    queryKey: ["settings"],
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

function HistoryFilters({
  window,
  setWindow,
  apply,
  clear,
}: {
  window: HistoryWindow;
  setWindow: (window: HistoryWindow) => void;
  apply: () => void;
  clear: () => void;
}) {
  const { t } = useTranslation();
  const vehicles = useQuery({ queryKey: ["vehicles"], queryFn: historyApi.vehicles });
  const [message, setMessage] = useState<string | null>(null);
  const options = vehicles.data?.items.map((vehicle) => ({
    value: String(vehicle.id),
    label: vehicle.name || vehicle.model || `${t("vehicle")} ${vehicle.id}`,
  })) ?? [];
  const submit = () => {
    const problem = validateWindow(window);
    setMessage(problem);
    if (!problem) apply();
  };
  return (
    <Card withBorder radius="md">
      <Stack gap="sm">
        <Text fw={600}>{t("filters")}</Text>
        {vehicles.error && <HistoryFailure retry={() => void vehicles.refetch()} />}
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          <Select
            label={t("vehicle")}
            placeholder={t("allVehicles")}
            clearable
            data={options}
            value={window.vehicleId === undefined ? null : String(window.vehicleId)}
            onChange={(id) => setWindow({ ...window, vehicleId: id ? Number(id) : undefined })}
            disabled={vehicles.isPending}
          />
          <TextInput
            label={t("fromUtc")}
            value={window.start}
            onChange={(event) => setWindow({ ...window, start: event.currentTarget.value })}
          />
          <TextInput
            label={t("toUtc")}
            value={window.end}
            onChange={(event) => setWindow({ ...window, end: event.currentTarget.value })}
          />
        </SimpleGrid>
        <Text size="sm" c="dimmed">{t("utcWindowHelp")}</Text>
        {message && <Alert color="red">{t(message)}</Alert>}
        <Group>
          <Button onClick={submit}>{t("apply")}</Button>
          <Button variant="subtle" onClick={() => { setMessage(null); clear(); }}>
            {t("clear")}
          </Button>
        </Group>
      </Stack>
    </Card>
  );
}

function HistoryList({ kind }: { kind: "trips" | "charges" }) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState(defaultWindow);
  const [active, setActive] = useState(draft);
  const [cursors, setCursors] = useState<string[]>([]);
  const cursor = cursors.at(-1);
  const preferences = useHistorySettings();
  const query = useQuery<HistoryPage>({
    queryKey: [kind, active, cursor],
    queryFn: () => kind === "trips"
      ? historyApi.trips({ ...active, cursor })
      : historyApi.charges({ ...active, cursor }),
    enabled: Boolean(preferences.data?.preferences?.saved),
  });
  const apply = () => { setCursors([]); setActive(draft); };
  const clear = () => {
    const reset = defaultWindow();
    setCursors([]);
    setDraft(reset);
    setActive(reset);
  };
  const page = query.data;
  if (preferences.isPending) return <Container py="xl"><Text>{t("loading")}</Text></Container>;
  if (preferences.error || !preferences.data?.preferences?.saved)
    return <Container py="xl"><HistoryFailure retry={() => void preferences.refetch()} /></Container>;
  const timezone = preferences.data.preferences.timezone;
  return (
    <Container size="md" py="xl">
      <Stack gap="lg">
        <Title order={1}>{t(kind)}</Title>
        <Text size="sm" c="dimmed">{t("timesShownIn", { timezone })}</Text>
        <HistoryFilters window={draft} setWindow={setDraft} apply={apply} clear={clear} />
        {query.isPending && <Text>{t("loading")}</Text>}
        {query.error && <HistoryFailure retry={() => void query.refetch()} />}
        {page?.items.length === 0 && <Alert>{t("noHistory")}</Alert>}
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
              <Button component={Link} variant="light" to={`/${kind}/${item.id}`}>{t("viewDetails")}</Button>
            </Stack>
          </Card>
        ))}
        <Group>
          {cursors.length > 0 && <Button variant="light" onClick={() => setCursors((value) => value.slice(0, -1))}>{t("previousPage")}</Button>}
          {page?.next_cursor && <Button variant="light" onClick={() => setCursors((value) => [...value, page.next_cursor!])}>{t("nextPage")}</Button>}
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
  const detail = useQuery<Trip | Charge>({
    queryKey: [kind, identifier],
    queryFn: () => kind === "trips" ? historyApi.trip(identifier) : historyApi.charge(identifier),
    enabled: Number.isInteger(identifier) && identifier > 0 && Boolean(settings.data?.preferences?.saved),
  });
  const trajectory = useQuery({ queryKey: ["trajectory", identifier], queryFn: () => historyApi.trajectory(identifier), enabled: kind === "trips" && detail.isSuccess && Boolean(settings.data?.preferences?.saved) });
  const [mapModuleFailed, setMapModuleFailed] = useState(false);
  const [tileFailed, setTileFailed] = useState(false);
  if (!Number.isInteger(identifier) || identifier <= 0) return <Container py="xl"><Alert color="red">{t("historyNotFound")}</Alert></Container>;
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
    <Button component={Link} variant="subtle" to={`/${kind}`}>{kind === "trips" ? t("backToTrips") : t("backToCharges")}</Button>
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
  const vehicles = useQuery({
    queryKey: ["vehicles"],
    queryFn: historyApi.vehicles,
    enabled: Boolean(settings.data?.preferences?.saved),
  });
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

export const TripsPage = () => <HistoryList kind="trips" />;
export const ChargesPage = () => <HistoryList kind="charges" />;
export const TripDetailPage = () => {
  const { id } = useParams();
  return <HistoryDetail key={id} kind="trips" />;
};
export const ChargeDetailPage = () => {
  const { id } = useParams();
  return <HistoryDetail key={id} kind="charges" />;
};
