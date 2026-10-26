// Theme contract tests (issue #12): toggling flips BOTH html.dark (the
// Tailwind dark variant) and document.body[data-theme] (the acceptance's
// body-level observability), the choice persists to localStorage under
// "bi-ui-theme", and a fresh provider mount reads it back.

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "../../lib/theme";
import { ThemeToggle } from "./theme-toggle";

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.classList.remove("dark");
  delete document.body.dataset.theme;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function mountToggle() {
  return render(
    <ThemeProvider>
      <ThemeToggle />
    </ThemeProvider>,
  );
}

describe("ThemeToggle", () => {
  it("starts light and applies the theme on mount", () => {
    mountToggle();
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(document.body.dataset.theme).toBe("light");
  });

  it("toggles html.dark and body[data-theme] together on click", async () => {
    const user = userEvent.setup();
    mountToggle();
    await user.click(screen.getByTestId("theme-toggle"));
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.body.dataset.theme).toBe("dark");
    await user.click(screen.getByTestId("theme-toggle"));
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(document.body.dataset.theme).toBe("light");
  });

  it("persists the choice to localStorage", async () => {
    const user = userEvent.setup();
    mountToggle();
    await user.click(screen.getByTestId("theme-toggle"));
    expect(window.localStorage.getItem("bi-ui-theme")).toBe("dark");
    await user.click(screen.getByTestId("theme-toggle"));
    expect(window.localStorage.getItem("bi-ui-theme")).toBe("light");
  });

  it("restores a stored dark theme on a fresh mount", () => {
    window.localStorage.setItem("bi-ui-theme", "dark");
    mountToggle();
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.body.dataset.theme).toBe("dark");
  });

  it("is reachable as a named control", () => {
    mountToggle();
    expect(screen.getByRole("button", { name: "Toggle theme" })).toBeTruthy();
  });
});
