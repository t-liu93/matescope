import {
  Alert,
  Badge,
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
import { Component, Fragment, lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { ApiError, historyApi, settingsApi, type HistoryWindow } from "./api/client";
import type { components } from "./api/schema";
import { addCalendarDays, defaultWindow, groupTrajectory, localDateIso, windowToCalendarRange, type CalendarRange } from "./history-utils";
import type { HistoryWindowPreset } from "./api/client";
import i18n from "./i18n";
import { useOnlineStatus } from "./pwa";
import { HistorySelectionGuard, useHistoryContext } from "./history-context";
import { historyReturn, historyScope, previousCursor, rememberCursor, rememberHistoryReturn } from "./history-navigation";
import { DualTimeSeriesChart, TimeSeriesChart } from "./time-series-chart";

type Trip = components["schemas"]["Trip"];
type Charge = components["schemas"]["Charge"];
type HistoryPage = components["schemas"]["TripPage"] | components["schemas"]["ChargePage"];
type TripPeriodSummary = components["schemas"]["TripPeriodSummary"];
type ChargePeriodSummary = components["schemas"]["ChargePeriodSummary"];
type TripSeries = components["schemas"]["TripSeries"];
type ChargeSeries = components["schemas"]["ChargeSeries"];
type TimeSeries = components["schemas"]["TimeSeries"];

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

function value(value: number | null | undefined, unit: string) {
  if (value == null) return <span className="metric-value metric-value-empty">—</span>;
  const formatted = value.toLocaleString(undefined, { maximumFractionDigits: 1 });
  return (
    <span className="metric" aria-label={`${formatted} ${unit}`}>
      <span className="metric-value">{formatted}</span>{" "}
      <span className="metric-unit">{unit}</span>
    </span>
  );
}

function whole(value: number | null | undefined) {
  if (value == null) return <span className="metric-value metric-value-empty">—</span>;
  return <span className="metric-value">{Math.round(value).toLocaleString()}</span>;
}

function unavailableSeries(name: TimeSeries["name"], unit: string): TimeSeries {
  return {
    name,
    unit,
    start: null,
    end: null,
    sample_count: 0,
    bucket_count: 0,
    aggregation: name === "battery" ? "last" : "mean_min_max",
    capability: { available: false, reason: "insufficient_permissions" },
    points: [],
  };
}

function signedWhole(value: number | null | undefined) {
  if (value == null) return <span className="metric-value metric-value-empty">—</span>;
  const rounded = Math.round(value);
  return <span className="metric-value">{rounded > 0 ? `+${rounded}` : rounded.toLocaleString()}</span>;
}

function duration(value: number | null | undefined) {
  if (value == null) return <span className="metric-value metric-value-empty">—</span>;
  const minutes = Math.round(value);
  const hours = Math.floor(minutes / 60);
  return <span className="metric-value">{hours ? `${hours}h ${minutes % 60}m` : `${minutes}m`}</span>;
}

function cost(value: number | null | undefined, currency: string | null | undefined, t: (key: string) => string) {
  if (value == null) return <span className="metric-value metric-value-empty">{t("costUnknown")}</span>;
  const formatted = currency
    ? new Intl.NumberFormat(undefined, { style: "currency", currency, minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value)
    : value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return <span className="metric-value">{formatted}</span>;
}

function localDay(value: string, timezone: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "full", timeZone: timezone }).format(new Date(value));
}

export function MetricCoverage({ coverage, t }: { coverage: components["schemas"]["MetricCoverage"]; t: (key: string, options?: Record<string, unknown>) => string }) {
  const reasonKeys = {
    no_ended_records: "metricNoEndedRecords",
    no_valid_values: "metricNoValidValues",
    zero_denominator: "metricZeroDenominator",
    unavailable: "metricUnavailable",
  } as const;
  return <Stack gap={0}>
    <Text size="xs" c="dimmed">{t("metricCoverage", { valid: coverage.valid_count, applicable: coverage.applicable_count })}</Text>
    {coverage.reason && <Text size="xs" c="dimmed">{t(reasonKeys[coverage.reason])}</Text>}
  </Stack>;
}

function HistoryFailure({ retry, error }: { retry: () => void; error?: unknown }) {
  const { t } = useTranslation();
  const sourceMessages: Record<string, string> = {
    unconfigured: "testCodeUnconfigured", disabled: "testCodeDisabled", skipped: "testCodeSkipped",
    invalid_credentials: "testCodeInvalidCredentials", unavailable: "testCodeUnavailable", timeout: "testCodeTimeout",
    incompatible_schema: "testCodeIncompatibleSchema", unsafe_permissions: "testCodeUnsafePermissions",
    insufficient_permissions: "testCodeInsufficientPermissions",
  };
  const sourceCode = error instanceof ApiError
    ? error.sourceCode
    : (typeof error === "object" && error !== null && "sourceCode" in error && typeof error.sourceCode === "string" ? error.sourceCode : undefined);
  const sourceMessage = sourceCode ? sourceMessages[sourceCode] : undefined;
  return (
    <Alert color="red">
      <Stack gap="xs">
        <Text>{t("historyLoadFailed")}</Text>
        {sourceMessage && <Text size="sm">{t(sourceMessage)}</Text>}
        <Button variant="light" onClick={retry}>{t("retry")}</Button>
      </Stack>
    </Alert>
  );
}

function OfflineVehicleData() {
  const { t } = useTranslation();
  return <Container py="xl"><Alert color="yellow">{t("offlineVehicleData")}</Alert></Container>;
}

function TripSummary({ summary, pending, error, retry }: { summary?: TripPeriodSummary; pending: boolean; error: unknown; retry: () => void }) {
  const { t } = useTranslation();
  if (pending) return <Card withBorder radius="md"><Text>{t("loading")}</Text></Card>;
  if (error || !summary) return <HistoryFailure retry={retry} error={error} />;
  return <Card withBorder radius="md" aria-label={t("tripPeriodSummary")}>
    <Stack gap="sm">
      <Title order={2}>{t("tripPeriodSummary")}</Title>
      <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
        <div><Text size="sm" c="dimmed">{t("records")}</Text><Text>{summary.total_count.toLocaleString()}</Text></div>
        <div><Text size="sm" c="dimmed">{t("distance")}</Text><Text>{value(summary.distance_km, "km")}</Text><MetricCoverage coverage={summary.distance_coverage} t={t} /></div>
        <div><Text size="sm" c="dimmed">{t("duration")}</Text><Text>{duration(summary.duration_min)}</Text><MetricCoverage coverage={summary.duration_coverage} t={t} /></div>
        <div><Text size="sm" c="dimmed">{t("estimatedEnergy")}</Text><Text>{value(summary.estimated_energy_kwh, "kWh")}</Text><MetricCoverage coverage={summary.estimated_energy_coverage} t={t} /></div>
      </SimpleGrid>
      {summary.not_ended_count > 0 && <Text size="sm" c="dimmed">{t("notEndedExcluded", { count: summary.not_ended_count })}</Text>}
    </Stack>
  </Card>;
}

function ChargeSummary({ summary, pending, error, retry }: { summary?: ChargePeriodSummary; pending: boolean; error: unknown; retry: () => void }) {
  const { t } = useTranslation();
  if (pending) return <Card withBorder radius="md"><Text>{t("loading")}</Text></Card>;
  if (error || !summary) return <HistoryFailure retry={retry} error={error} />;
  const currencyConfigured = summary.currency !== null;
  return <Card withBorder radius="md" aria-label={t("chargePeriodSummary")}>
    <Stack gap="sm">
      <Title order={2}>{t("chargePeriodSummary")}</Title>
      <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
        <div><Text size="sm" c="dimmed">{t("records")}</Text><Text>{summary.total_count.toLocaleString()}</Text></div>
        <div><Text size="sm" c="dimmed">{t("energyAdded")}</Text><Text>{value(summary.energy_added_kwh, "kWh")}</Text><MetricCoverage coverage={summary.energy_added_coverage} t={t} /></div>
        <div><Text size="sm" c="dimmed">{t("duration")}</Text><Text>{duration(summary.duration_min)}</Text><MetricCoverage coverage={summary.duration_coverage} t={t} /></div>
        <div><Text size="sm" c="dimmed">{t("recordedCost")}</Text><Text>{currencyConfigured ? cost(summary.cost, summary.currency, t) : t("currencyNotConfigured")}</Text><MetricCoverage coverage={summary.cost_coverage} t={t} /></div>
      </SimpleGrid>
      {summary.not_ended_count > 0 && <Text size="sm" c="dimmed">{t("notEndedExcluded", { count: summary.not_ended_count })}</Text>}
    </Stack>
  </Card>;
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
  const location = useLocation();
  const navigate = useNavigate();
  const [draft, setDraft] = useState(defaultWindow);
  const search = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const cursor = search.get("cursor") ?? undefined;
  const pageNumber = Math.max(1, Number(search.get("page")) || 1);
  const scope = historyScope(location.pathname, search);
  const preferences = useHistorySettings();
  const online = useOnlineStatus();
  useEffect(() => { if (scopedWindow) setDraft(scopedWindow); }, [scopedWindow]);
  useEffect(() => { rememberCursor(scope, cursor ?? null); }, [cursor, scope]);
  const query = useQuery<HistoryPage>({
    queryKey: [kind, vehicle?.id, scopedWindow?.start, scopedWindow?.end, preferences.data?.preferences?.range_basis, cursor],
    queryFn: ({ signal }) => kind === "trips"
      ? historyApi.trips({ ...scopedWindow!, vehicleId: vehicle!.id, cursor }, { signal })
      : historyApi.charges({ ...scopedWindow!, vehicleId: vehicle!.id, cursor }, { signal }),
    enabled: online && !emptyWindow && Boolean(preferences.data?.preferences?.saved) && Boolean(vehicle && scopedWindow),
  });
  const tripSummary = useQuery<TripPeriodSummary>({
    queryKey: ["trip-summary", vehicle?.id, scopedWindow?.start, scopedWindow?.end, preferences.data?.preferences?.range_basis],
    queryFn: ({ signal }) => historyApi.tripSummary({ ...scopedWindow!, vehicleId: vehicle!.id }, { signal }),
    enabled: kind === "trips" && online && !emptyWindow && Boolean(preferences.data?.preferences?.saved) && Boolean(vehicle && scopedWindow),
  });
  const chargeSummary = useQuery<ChargePeriodSummary>({
    queryKey: ["charge-summary", vehicle?.id, scopedWindow?.start, scopedWindow?.end, preferences.data?.preferences?.display_currency],
    queryFn: ({ signal }) => historyApi.chargeSummary({ ...scopedWindow!, vehicleId: vehicle!.id }, { signal }),
    enabled: kind === "charges" && online && !emptyWindow && Boolean(preferences.data?.preferences?.saved) && Boolean(vehicle && scopedWindow),
  });
  const clear = () => {
    if (scopedWindow) setDraft(scopedWindow);
  };
  const page = query.data;
  useEffect(() => {
    if (!page?.items.length) return;
    const target = historyReturn(`${location.pathname}${location.search}`);
    if (!target) return;
    const frame = window.requestAnimationFrame(() => {
      const record = document.querySelector<HTMLElement>(`[data-history-record-id="${target.recordId}"]`);
      record?.focus({ preventScroll: true });
      window.scrollTo({ top: target.scrollY, behavior: "auto" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [location.pathname, location.search, page?.items.length]);
  const moveToCursor = (nextCursor: string | null, nextPage: number) => {
    const next = new URLSearchParams(location.search);
    if (nextCursor) next.set("cursor", nextCursor); else next.delete("cursor");
    if (nextPage > 1) next.set("page", String(nextPage)); else next.delete("page");
    navigate(`${location.pathname}?${next.toString()}`);
  };
  const priorCursor = previousCursor(scope, cursor ?? null);
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
        {kind === "trips" && !emptyWindow && <TripSummary summary={tripSummary.data} pending={tripSummary.isPending} error={tripSummary.error} retry={() => void tripSummary.refetch()} />}
        {kind === "charges" && !emptyWindow && <ChargeSummary summary={chargeSummary.data} pending={chargeSummary.isPending} error={chargeSummary.error} retry={() => void chargeSummary.refetch()} />}
        {query.isPending && <Text>{t("loading")}</Text>}
        {query.error && <HistoryFailure retry={() => void query.refetch()} />}
        {(emptyWindow || page?.items.length === 0) && <Alert>{t("noHistory")}</Alert>}
        {kind === "trips" ? <TripRows items={(page?.items ?? []) as Trip[]} timezone={timezone} historyPath={historyPath} listPath={`${location.pathname}${location.search}`} /> : <ChargeRows items={(page?.items ?? []) as Charge[]} timezone={timezone} historyPath={historyPath} listPath={`${location.pathname}${location.search}`} currency={preferences.data.preferences.display_currency ?? null} />}
        <Group>
          {priorCursor !== undefined && <Button variant="light" onClick={() => moveToCursor(priorCursor, pageNumber - 1)}>{t("previousPage")}</Button>}
          {page?.next_cursor && <Button variant="light" onClick={() => moveToCursor(page.next_cursor!, pageNumber + 1)}>{t("nextPage")}</Button>}
        </Group>
      </Stack>
    </Container>
  );
}

function RecordDetailLink({ to, listPath, recordId }: { to: string; listPath: string; recordId: number }) {
  const { t } = useTranslation();
  return <Button component={Link} variant="light" to={to} state={{ historyReturnPath: listPath }} onClick={() => rememberHistoryReturn(listPath, recordId, window.scrollY)} data-history-record-id={recordId}>{t("viewDetails")}</Button>;
}

function ChargeRows({ items, timezone, historyPath, listPath, currency }: { items: Charge[]; timezone: string; historyPath: (pathname: string, options?: { includeCursor?: boolean }) => string; listPath: string; currency: string | null }) {
  const { t } = useTranslation();
  const groups = useMemo(() => {
    const ordered = [...items].sort((a, b) => b.start.localeCompare(a.start) || b.id - a.id);
    return ordered.reduce<{ label: string; items: Charge[] }[]>((acc, item) => {
      const label = localDay(item.start, timezone);
      const group = acc.at(-1);
      if (group?.label === label) group.items.push(item); else acc.push({ label, items: [item] });
      return acc;
    }, []);
  }, [items, timezone]);
  return <Stack gap="sm">{groups.map((group) => <Fragment key={group.label}>
    <Text fw={600} size="sm" c="dimmed">{group.label}</Text>
    {group.items.map((item) => <Card key={item.id} withBorder radius="md" className="charge-list-card">
      <Stack gap="xs">
        <Group justify="space-between" align="start" wrap="nowrap"><Text fw={600}>{formatDate(item.start, timezone)}</Text><Text size="sm" c="dimmed">{item.end ? duration(item.duration_min) : t("recordNotEnded")}</Text></Group>
        <Text className="charge-place" title={item.place ?? t("unknownLocation")}>{item.place ?? t("unknownLocation")}</Text>
        <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="xs">
          <Text>{t("soc")}: {whole(item.start_battery_level)} → {whole(item.end_battery_level)}</Text>
          <Text>{t("energyAdded")}: {value(item.energy_added_kwh, "kWh")}</Text>
          <Text>{t("duration")}: {item.end ? duration(item.duration_min) : t("recordNotEnded")}</Text>
          <Text>{t("recordedCost")}: {cost(item.cost, currency, t)} {!currency && item.cost != null && <span className="currency-unconfigured">{t("currencyNotConfigured")}</span>}</Text>
        </SimpleGrid>
        <RecordDetailLink to={historyPath(`/charges/${item.id}`, { includeCursor: true })} listPath={listPath} recordId={item.id} />
      </Stack>
    </Card>)}
  </Fragment>)}</Stack>;
}

function TripRows({ items, timezone, historyPath, listPath }: { items: Trip[]; timezone: string; historyPath: (pathname: string, options?: { includeCursor?: boolean }) => string; listPath: string }) {
  const { t } = useTranslation();
  const desktop = useMediaQuery("(min-width: 1200px)");
  const groups = useMemo(() => {
    const ordered = [...items].sort((a, b) => b.start.localeCompare(a.start) || b.id - a.id);
    return ordered.reduce<{ label: string; items: Trip[] }[]>((acc, item) => {
      const label = localDay(item.start, timezone);
      const group = acc.at(-1);
      if (group?.label === label) group.items.push(item); else acc.push({ label, items: [item] });
      return acc;
    }, []);
  }, [items, timezone]);
  return <Stack gap="sm">{groups.map((group) => <Fragment key={group.label}>
    <Text fw={600} size="sm" c="dimmed">{group.label}</Text>
    {group.items.map((item) => <Card key={item.id} withBorder radius="md" className="trip-list-card">
      <Stack gap="xs">
        <Group justify="space-between" align="start" wrap="nowrap"><Text fw={600}>{formatDate(item.start, timezone)}</Text><Text size="sm" c="dimmed">{item.end ? duration(item.duration_min) : t("recordNotEnded")}</Text></Group>
        <Text className="trip-place" title={`${item.start_place ?? t("unknownLocation")} → ${item.end_place ?? t("unknownLocation")}`}>{item.start_place ?? t("unknownLocation")} <span aria-hidden="true">→</span> {item.end_place ?? t("unknownLocation")}</Text>
        <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="xs">
          <Text>{t("distance")}: {value(item.distance_km, "km")}</Text>
          <Text>{t("soc")}: {whole(item.start_battery_level)} → {whole(item.end_battery_level)}</Text>
          <Text>{t("estimatedEnergy")}: {value(item.estimated_energy_kwh, "kWh")}</Text>
          {desktop && <Text>{t("estimatedConsumption")}: {value(item.estimated_average_consumption_wh_per_km, "Wh/km")}</Text>}
        </SimpleGrid>
        <RecordDetailLink to={historyPath(`/trips/${item.id}`, { includeCursor: true })} listPath={listPath} recordId={item.id} />
      </Stack>
    </Card>)}
  </Fragment>)}</Stack>;
}

export function DetailValues({ item, kind, timezone, rangeBasis, currency = null }: { item: Trip | Charge; kind: "trips" | "charges"; timezone: string; rangeBasis: "rated" | "ideal"; currency?: string | null }) {
  const { t } = useTranslation();
  if (kind === "charges") return <ChargeDetailValues item={item as Charge} timezone={timezone} currency={currency} />;
  const trip = item as Trip;
  return <Stack gap="sm">
    <Group justify="space-between" align="start" wrap="wrap">
      <Text className="trip-detail-place"><span className="detail-label">{t("start")}</span> {trip.start_place ?? t("unknownLocation")}</Text>
      <Text className="trip-detail-place"><span className="detail-label">{t("end")}</span> {trip.end_place ?? t("unknownLocation")}</Text>
    </Group>
    <SimpleGrid cols={{ base: 2, sm: 3 }} spacing="sm">
      <Text>{t("date")}: {formatDate(item.start, timezone)}{item.end ? ` → ${formatDate(item.end, timezone)}` : ` · ${t("unfinished")}`}</Text>
      <Text>{t("duration")}: {duration(item.duration_min)}</Text>
      <Text>{t("distance")}: {value(trip.distance_km, "km")}</Text>
      <Text>{t("soc")}: {whole(trip.start_battery_level)} → {whole(trip.end_battery_level)}</Text>
      <Text>{t("maxSpeed")}: {value(trip.speed_max_kmh, "km/h")}</Text>
      <div>
        <Text component="span">{t("estimatedEnergy")}: {value(trip.estimated_energy_kwh, "kWh")} <Badge size="xs" variant="light">{t("estimated")}</Badge></Text>
        <Text size="xs" c="dimmed">{t("estimatedEnergyBasis", { basis: t(`rangeBasis_${rangeBasis}`) })}</Text>
      </div>
    </SimpleGrid>
  </Stack>;
}

export function ChargeDetailValues({ item, timezone, currency = null }: { item: Charge; timezone: string; currency?: string | null }) {
  const { t } = useTranslation();
  const socChange = item.start_battery_level != null && item.end_battery_level != null
    ? item.end_battery_level - item.start_battery_level
    : null;
  const ended = item.end !== null;
  return <Stack gap="sm" className="charge-detail-values">
    <Text className="charge-detail-place"><span className="detail-label">{t("location")}</span> {item.place ?? t("unknownLocation")}</Text>
    {!ended && <Alert color="yellow" role="status">{t("provisionalCharge")}</Alert>}
    <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
      <Text><span className="detail-label">{t("start")}</span> {formatDate(item.start, timezone)}</Text>
      <Text><span className="detail-label">{t("end")}</span> {ended ? formatDate(item.end, timezone) : t("recordNotEnded")}</Text>
      <Text><span className="detail-label">{t("duration")}</span> {ended ? duration(item.duration_min) : t("provisional")}</Text>
      <Text><span className="detail-label">{t("soc")}</span> {whole(item.start_battery_level)} → {whole(item.end_battery_level)}</Text>
      <Text><span className="detail-label">{t("socChange")}</span> {signedWhole(socChange)}%</Text>
      <Text><span className="detail-label">{t("energyAdded")}</span> {value(item.energy_added_kwh, "kWh")}</Text>
      <Text><span className="detail-label">{t("recordedEnergyUsed")}</span> {value(item.recorded_energy_used_kwh, "kWh")}</Text>
      <Text><span className="detail-label">{t("recordedCost")}</span> {cost(item.cost, currency, t)} {!currency && <span className="currency-unconfigured">{t("currencyNotConfigured")}</span>}</Text>
    </SimpleGrid>
  </Stack>;
}

export function TripMoreData({ series, timezone }: { series: TripSeries["series"]; timezone: string }) {
  const { t } = useTranslation();
  const insideTemperature = series.find((item) => item.name === "inside_temperature");
  const outsideTemperature = series.find((item) => item.name === "outside_temperature");
  const elevation = series.find((item) => item.name === "elevation");
  const temperatureTitle = t("tripTemperature");

  return <details className="trip-more-data">
    <summary>{t("tripMoreData")}</summary>
    <div className="trip-more-data-grid">
      <Card withBorder radius="sm"><Stack gap="sm">
        <Title order={3}>{temperatureTitle}</Title>
        {!insideTemperature && !outsideTemperature && <Alert color="yellow" role="status">{t("tripTemperatureUnavailable")}</Alert>}
        {insideTemperature && outsideTemperature
          ? <DualTimeSeriesChart first={insideTemperature} second={outsideTemperature} timezone={timezone} title={temperatureTitle} firstTitle={t("insideTemperature")} secondTitle={t("outsideTemperature")} />
          : insideTemperature
            ? <><TimeSeriesChart series={insideTemperature} timezone={timezone} title={t("insideTemperature")} /><Alert color="yellow" role="status">{t("outsideTemperatureUnavailable")}</Alert></>
            : outsideTemperature
              ? <><TimeSeriesChart series={outsideTemperature} timezone={timezone} title={t("outsideTemperature")} /><Alert color="yellow" role="status">{t("insideTemperatureUnavailable")}</Alert></>
              : null}
      </Stack></Card>
      <Card withBorder radius="sm"><Stack gap="sm">
        <Title order={3}>{t("tripElevation")}</Title>
        {elevation ? <TimeSeriesChart series={elevation} timezone={timezone} title={t("tripElevation")} /> : <Alert color="yellow" role="status">{t("tripElevationUnavailable")}</Alert>}
      </Stack></Card>
    </div>
  </details>;
}

function HistoryDetail({ kind }: { kind: "trips" | "charges" }) {
  const { t } = useTranslation();
  const params = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const identifier = Number(params.id);
  const settings = useHistorySettings();
  const online = useOnlineStatus();
  const { setVehicleId, historyPath, vehicle } = useHistoryContext();
  const returnPath = typeof location.state === "object" && location.state !== null && "historyReturnPath" in location.state && typeof location.state.historyReturnPath === "string"
    ? location.state.historyReturnPath : null;
  const detail = useQuery<Trip | Charge>({
    queryKey: [kind, identifier],
    queryFn: ({ signal }) => kind === "trips" ? historyApi.trip(identifier, { signal }) : historyApi.charge(identifier, { signal }),
    enabled: online && Number.isInteger(identifier) && identifier > 0 && Boolean(settings.data?.preferences?.saved),
    staleTime: Infinity,
  });
  const trajectory = useQuery({ queryKey: ["trajectory", identifier], queryFn: ({ signal }) => historyApi.trajectory(identifier, { signal }), enabled: online && kind === "trips" && detail.isSuccess && Boolean(settings.data?.preferences?.saved) });
  const tripSeries = useQuery<TripSeries>({
    queryKey: ["trip-series", identifier],
    queryFn: ({ signal }) => historyApi.tripSeries(identifier, { signal }),
    enabled: online && kind === "trips" && detail.isSuccess && Boolean(settings.data?.preferences?.saved),
  });
  const chargeSeries = useQuery<ChargeSeries>({
    queryKey: ["charge-series", identifier],
    queryFn: ({ signal }) => historyApi.chargeSeries(identifier, { signal }),
    enabled: online && kind === "charges" && detail.isSuccess && Boolean(settings.data?.preferences?.saved),
  });
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
  const rangeBasis = settings.data.preferences.range_basis ?? "rated";
  const vehicleLabel = vehicle?.name || vehicle?.model || `${t("vehicle")} #${item.vehicle_id}`;
  return <Container size="md" py="xl"><Stack gap="lg">
    <Button variant="subtle" onClick={() => returnPath ? navigate(-1) : navigate(historyPath(`/${kind}`, { includeCursor: true }))}>{kind === "trips" ? t("backToTrips") : t("backToCharges")}</Button>
    <Title order={1}>{kind === "trips" ? t("trip") : t("charge")}</Title>
    <Group justify="space-between" align="start" wrap="wrap">
      <Text size="sm" c="dimmed">{vehicleLabel} · #{item.vehicle_id}</Text>
      <Text size="sm" c="dimmed">{formatDate(item.start, timezone)}</Text>
    </Group>
    <Text size="sm" c="dimmed">{t("timesShownIn", { timezone })}</Text>
    <div className={kind === "trips" ? "trip-detail-layout" : undefined}>
      <Card withBorder radius="md"><Stack gap="sm"><Title order={2}>{t("summary")}</Title><DetailValues item={item} kind={kind} timezone={timezone} rangeBasis={rangeBasis} currency={settings.data.preferences.display_currency ?? null} /></Stack></Card>
      {kind === "trips" && <Card key={identifier} withBorder radius="md"><Stack><Title order={2}>{t("route")}</Title>
      {trajectory.isPending && <Text>{t("loadingMap")}</Text>}
      {(trajectory.error || mapModuleFailed || tileFailed) && <Alert color="yellow">{t("mapUnavailable")}</Alert>}
      {trajectory.data?.points.length === 0 && <Alert>{t("noTrajectory")}</Alert>}
      {trajectory.data && trajectory.data.points.length > 0 && !mapModuleFailed && <MapBoundary failed={() => setMapModuleFailed(true)}><Suspense fallback={<Text>{t("loadingMap")}</Text>}><TrajectoryMap points={groupTrajectory(trajectory.data.points)} tileUrl={settings.data.preferences.tile_url} onTileError={() => setTileFailed(true)} /></Suspense></MapBoundary>}
      {trajectory.data?.simplified && <Text size="sm" c="dimmed">{t("trajectorySimplified", { count: trajectory.data.total_points })}</Text>}
      </Stack></Card>}
    </div>
    {kind === "trips" && <Card key={`series-${identifier}`} withBorder radius="md"><Stack gap="sm">
      {tripSeries.isPending && <Text>{t("loading")}</Text>}
      {tripSeries.error && <Alert color="yellow">{t("tripSeriesUnavailable")}</Alert>}
      {tripSeries.data && (() => {
        const speed = tripSeries.data.series.find((series) => series.name === "speed");
        const power = tripSeries.data.series.find((series) => series.name === "power");
        const battery = tripSeries.data.series.find((series) => series.name === "battery");
        return <div className="trip-chart-section">
          <Card withBorder radius="sm"><Stack gap="sm"><Title order={2}>{t("tripSpeedPower")}</Title>
            {!speed || !power
              ? <Alert color="yellow">{t("tripSeriesUnavailable")}</Alert>
              : <DualTimeSeriesChart first={speed} second={power} timezone={timezone} title={t("tripSpeedPower")} firstTitle={t("speed")} secondTitle={t("power")} />}
          </Stack></Card>
          <Card withBorder radius="sm"><Stack gap="sm"><Title order={2}>{t("tripSoc")}</Title>
            {!battery
              ? <Alert color="yellow">{t("tripSocUnavailable")}</Alert>
              : <TimeSeriesChart series={battery} timezone={timezone} title={t("tripSoc")} />}
          </Stack></Card>
          <TripMoreData series={tripSeries.data.series} timezone={timezone} />
        </div>;
      })()}
    </Stack></Card>}
    {kind === "charges" && <Card key={`series-${identifier}`} withBorder radius="md"><Stack gap="sm">
      <Title order={2}>{t("chargePowerSoc")}</Title>
      {chargeSeries.isPending && <Text>{t("loading")}</Text>}
      {chargeSeries.error && <Alert color="yellow">{t("chargeSeriesUnavailable")}</Alert>}
      {chargeSeries.data && (() => {
        const power = chargeSeries.data.series.find((series) => series.name === "power") ?? unavailableSeries("power", "kW");
        const battery = chargeSeries.data.series.find((series) => series.name === "battery") ?? unavailableSeries("battery", "%");
        return <DualTimeSeriesChart first={power} second={battery} timezone={timezone} title={t("chargePowerSoc")} firstTitle={t("power")} secondTitle={t("soc")} />;
      })()}
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
