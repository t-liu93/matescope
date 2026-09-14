import { CircleMarker, MapContainer, Polyline, TileLayer, useMap } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";

type Props = {
  points: { latitude: number; longitude: number }[][];
  tileUrl: string;
  onTileError: () => void;
};

function FitRoute({ points }: Pick<Props, "points">) {
  const map = useMap();
  useEffect(() => {
    const locations = points.flat().map((point) => [point.latitude, point.longitude] as [number, number]);
    if (locations.length > 1) map.fitBounds(locations, { padding: [20, 20] });
    else if (locations[0]) map.setView(locations[0], 14);
  }, [map, points]);
  return null;
}

export default function TrajectoryMap({ points, tileUrl, onTileError }: Props) {
  const { t } = useTranslation();
  const first = points[0]?.[0];
  if (!first) return null;
  return (
    <div role="region" aria-label={t("tripRouteMap")}>
      <MapContainer
        center={[first.latitude, first.longitude]}
        zoom={14}
        scrollWheelZoom={false}
        className="trajectory-map"
      >
        <TileLayer
          url={tileUrl}
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          eventHandlers={{ tileerror: onTileError }}
        />
        <FitRoute points={points} />
        {points.map((segment, index) => (
          segment.length === 1
            ? <CircleMarker key={index} center={[segment[0].latitude, segment[0].longitude]} radius={6} />
            : <Polyline key={index} positions={segment.map((point) => [point.latitude, point.longitude])} />
        ))}
      </MapContainer>
    </div>
  );
}
