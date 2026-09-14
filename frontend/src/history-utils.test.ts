import { describe, expect, it } from "vitest";
import { defaultWindow, groupTrajectory, validateWindow } from "./history-utils";

describe("history window", () => {
  it("keeps UTC boundaries stable across a DST change", () => {
    const now = new Date("2026-03-29T12:00:00.000Z");
    const window = defaultWindow(now);
    expect(window).toEqual({
      start: "2026-02-27T12:00:00.000Z",
      end: "2026-03-29T12:00:00.000Z",
    });
    expect(validateWindow(window)).toBeNull();
  });

  it("rejects invalid and oversized windows", () => {
    expect(validateWindow({ start: "not-a-date", end: "2026-01-02T00:00:00Z" })).toBe("historyInvalidUtc");
    expect(validateWindow({ start: "2026-01-01T00:00:00", end: "2026-01-02T00:00:00Z" })).toBe("historyInvalidUtc");
    expect(validateWindow({ start: "2026-01-01T00:00:00+01:00", end: "2026-01-02T00:00:00Z" })).toBe("historyInvalidUtc");
    expect(validateWindow({ start: "2026-02-30T00:00:00Z", end: "2026-03-02T00:00:00Z" })).toBe("historyInvalidUtc");
    expect(validateWindow({ start: "2026-01-01T00:00:00Z", end: "2026-04-02T00:00:00Z" })).toBe("historyWindowTooLarge");
  });
});

describe("trajectory grouping", () => {
  it("never connects points from different server segments", () => {
    const groups = groupTrajectory([
      { id: 1, time: "2026-01-01T00:00:00Z", latitude: 1, longitude: 2, segment_id: 4 },
      { id: 2, time: "2026-01-01T00:01:00Z", latitude: 2, longitude: 3, segment_id: 4 },
      { id: 3, time: "2026-01-01T00:02:00Z", latitude: 3, longitude: 4, segment_id: 5 },
    ]);
    expect(groups.map((group) => group.map((point) => point.id))).toEqual([[1, 2], [3]]);
  });
});
