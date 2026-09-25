import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Vitest runs without globals, so Testing Library cannot register its own
// automatic cleanup; unmount rendered trees between tests explicitly.
afterEach(() => cleanup());

// jsdom does not implement layout APIs used by the inbox timeline.
if (typeof Element !== "undefined" && !Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
}
