import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom lacks the observers/media APIs antd v6 components reach for
// (rc-resize-observer, responsive layouts, virtual lists). Polyfill the
// no-op versions so component tests exercise behavior, not environment.
if (typeof window !== "undefined") {
  if (typeof window.ResizeObserver === "undefined") {
    class ResizeObserverStub {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    window.ResizeObserver =
      ResizeObserverStub as unknown as typeof ResizeObserver;
  }
  if (typeof window.matchMedia === "undefined") {
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener() {},
      removeListener() {},
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent: () => false,
    })) as typeof window.matchMedia;
  }
  if (typeof Element.prototype.scrollIntoView === "undefined") {
    Element.prototype.scrollIntoView = () => {};
  }
  if (typeof Element.prototype.scrollTo === "undefined") {
    Element.prototype.scrollTo = () => {};
  }
}

afterEach(() => {
  cleanup();
});
