import { beforeEach, describe, expect, it } from "vitest";

import { clearHistoryNavigationState, cursorTrail, historyReturn, historyScope, previousCursor, rememberCursor, rememberHistoryReturn } from "./history-navigation";

describe("history navigation metadata", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("keeps cursor pages scoped to vehicle and resolved window", () => {
    const scope = historyScope("/trips", new URLSearchParams("vehicle=1&start=2026-01-01T00%3A00%3A00Z&end=2026-02-01T00%3A00%3A00Z&cursor=opaque&page=2"));
    expect(scope).toBe("/trips?vehicle=1&start=2026-01-01T00%3A00%3A00Z&end=2026-02-01T00%3A00%3A00Z");
    rememberCursor(scope, null);
    rememberCursor(scope, "opaque");
    expect(cursorTrail(scope)).toEqual([null, "opaque"]);
    expect(previousCursor(scope, "opaque")).toBeNull();
  });

  it("retains a return target for repeated history traversal and clears all metadata on vehicle-data cleanup", () => {
    rememberHistoryReturn("/charges?vehicle=1", 42, 640);
    expect(historyReturn("/charges?vehicle=1")).toEqual({ recordId: 42, scrollY: 640 });
    expect(historyReturn("/charges?vehicle=1")).toEqual({ recordId: 42, scrollY: 640 });
    rememberCursor("/charges?vehicle=1", "opaque");
    clearHistoryNavigationState();
    expect(cursorTrail("/charges?vehicle=1")).toEqual([null]);
    expect(historyReturn("/charges?vehicle=1")).toBeUndefined();
  });
});
