// jsdom lays nothing out, so it has no ResizeObserver: an inert one lets
// components that measure themselves render in tests.
class InertResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver ??= InertResizeObserver
