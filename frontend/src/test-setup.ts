import "@testing-library/jest-dom/vitest";

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({ matches: false, media: query, onchange: null, addListener: () => undefined, removeListener: () => undefined, addEventListener: () => undefined, removeEventListener: () => undefined, dispatchEvent: () => false }),
});

class ResizeObserverMock {
  observe() { /* no layout in jsdom */ }
  unobserve() { /* no layout in jsdom */ }
  disconnect() { /* no layout in jsdom */ }
}

Object.defineProperty(window, "ResizeObserver", { writable: true, value: ResizeObserverMock });
