const navigationKey = "matescope-history-navigation";

type CursorTrail = Record<string, (string | null)[]>;
type ReturnTarget = { recordId: number; scrollY: number };
type NavigationState = { cursors: CursorTrail; returns: Record<string, ReturnTarget> };

function readState(): NavigationState {
  try {
    const value = sessionStorage.getItem(navigationKey);
    if (!value) return { cursors: {}, returns: {} };
    const parsed: unknown = JSON.parse(value);
    if (typeof parsed !== "object" || parsed === null) return { cursors: {}, returns: {} };
    const state = parsed as Partial<NavigationState>;
    return { cursors: state.cursors ?? {}, returns: state.returns ?? {} };
  } catch {
    return { cursors: {}, returns: {} };
  }
}

function writeState(state: NavigationState) {
  try { sessionStorage.setItem(navigationKey, JSON.stringify(state)); } catch { /* navigation restoration is optional */ }
}

export function historyScope(pathname: string, search: URLSearchParams) {
  const scope = new URLSearchParams(search);
  scope.delete("cursor");
  scope.delete("page");
  return `${pathname}?${scope.toString()}`;
}

export function cursorTrail(scope: string) {
  return readState().cursors[scope] ?? [null];
}

export function rememberCursor(scope: string, cursor: string | null) {
  const state = readState();
  const trail = state.cursors[scope] ?? [null];
  if (trail.at(-1) !== cursor) trail.push(cursor);
  state.cursors[scope] = trail;
  writeState(state);
}

export function previousCursor(scope: string, cursor: string | null) {
  const trail = cursorTrail(scope);
  const index = trail.lastIndexOf(cursor);
  return index > 0 ? trail[index - 1] : undefined;
}

export function rememberHistoryReturn(path: string, recordId: number, scrollY: number) {
  const state = readState();
  state.returns[path] = { recordId, scrollY };
  writeState(state);
}

export function historyReturn(path: string) {
  return readState().returns[path];
}

/** This state contains only opaque cursors and return-position metadata. */
export function clearHistoryNavigationState() {
  try { sessionStorage.removeItem(navigationKey); } catch { /* unavailable storage must not block cleanup */ }
}
