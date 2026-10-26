// Issue #12: the app's theme state. A plain React context instead of
// next-themes (Next.js-branded and html-attribute-only): the acceptance
// contract observes the theme on BOTH html.dark (the Tailwind @custom-variant
// hooks into this) and document.body[data-theme], so applyTheme writes both.
// Choice persists to localStorage under "bi-ui-theme"; a fresh provider mount
// reads it back so the toggle survives reloads.

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";

export type Theme = "light" | "dark";

const STORAGE_KEY = "bi-ui-theme";

// Anything but a stored "dark" falls back to light: corrupt entries and
// sandboxed storage (localStorage can throw) both degrade to the default.
function readStoredTheme(): Theme {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "dark"
      ? "dark"
      : "light";
  } catch {
    return "light";
  }
}

function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle("dark", theme === "dark");
  document.body.dataset.theme = theme;
}

interface ThemeContextValue {
  theme: Theme;
  toggle: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(readStoredTheme);

  // Apply on mount too (first paint parity for a stored non-default theme),
  // not only on changes, and mirror every applied choice into localStorage so
  // a fresh mount restores it.
  useEffect(() => {
    applyTheme(theme);
    try {
      window.localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // Sandboxed storage can deny writes; the session theme still applies.
    }
  }, [theme]);

  const toggle = useCallback(() => {
    setTheme((current) => (current === "dark" ? "light" : "dark"));
  }, []);

  return (
    <ThemeContext.Provider value={{ theme, toggle }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error("useTheme must be used within <ThemeProvider>");
  }
  return context;
}
