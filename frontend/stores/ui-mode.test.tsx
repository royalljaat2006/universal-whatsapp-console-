import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  UI_MODE_KEY,
  UiModeProvider,
  parseMode,
  readStoredMode,
  resetModeForTests,
  useUiMode,
} from "./ui-mode";

function Probe() {
  const { mode, isSimple, setMode } = useUiMode();
  return (
    <div>
      <span data-testid="mode">{mode}</span>
      <span data-testid="simple">{String(isSimple)}</span>
      <button onClick={() => setMode("advanced")}>advanced</button>
      <button onClick={() => setMode("simple")}>simple</button>
    </div>
  );
}

beforeEach(() => {
  resetModeForTests();
  window.localStorage.clear();
});
afterEach(() => vi.restoreAllMocks());

describe("ui mode", () => {
  it("defaults to simple", () => {
    render(
      <UiModeProvider>
        <Probe />
      </UiModeProvider>,
    );
    expect(screen.getByTestId("mode")).toHaveTextContent("simple");
    expect(screen.getByTestId("simple")).toHaveTextContent("true");
  });

  it("switches, persists, and a new provider reads the stored choice", () => {
    const first = render(
      <UiModeProvider>
        <Probe />
      </UiModeProvider>,
    );
    act(() => screen.getByText("advanced").click());
    expect(screen.getByTestId("mode")).toHaveTextContent("advanced");
    expect(window.localStorage.getItem(UI_MODE_KEY)).toBe("advanced");
    first.unmount();

    resetModeForTests(); // simulate a fresh page load: storage is the only source
    render(
      <UiModeProvider>
        <Probe />
      </UiModeProvider>,
    );
    expect(screen.getByTestId("mode")).toHaveTextContent("advanced");
  });

  it("ignores a corrupt stored value", () => {
    window.localStorage.setItem(UI_MODE_KEY, "expert");
    expect(readStoredMode()).toBe("simple");
    expect(parseMode("expert")).toBeNull();
    expect(parseMode("advanced")).toBe("advanced");
  });

  it("still switches for this page when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    render(
      <UiModeProvider>
        <Probe />
      </UiModeProvider>,
    );
    expect(screen.getByTestId("mode")).toHaveTextContent("simple");
    act(() => screen.getByText("advanced").click());
    expect(screen.getByTestId("mode")).toHaveTextContent("advanced");
  });

  it("refuses to be used outside its provider", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Probe />)).toThrow(/UiModeProvider/);
  });
});
