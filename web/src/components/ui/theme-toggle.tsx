"use client";
// Adapted from beui.dev/components/motion/theme-toggle: keeps beUI's
// Sun/Moon swap behind a motion transition. Dropped from the original: the
// next-themes dependency, ActionSwapIcon and the ~200 lines of
// view-transition CSS — this app is a Vite SPA whose theme state lives in
// web/src/lib/theme.tsx, and the plain swap needs none of that machinery.

import { AnimatePresence, motion } from "motion/react";
import { Moon, Sun } from "lucide-react";
import { EASE_OUT } from "../../lib/ease";
import { useTheme } from "../../lib/theme";

export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  return (
    <button
      type="button"
      data-testid="theme-toggle"
      aria-label="Toggle theme"
      onClick={toggle}
      className="inline-flex size-9 shrink-0 items-center justify-center rounded-lg border border-border bg-card text-foreground outline-none transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring"
    >
      <AnimatePresence initial={false} mode="wait">
        <motion.span
          key={theme}
          initial={{ opacity: 0, rotate: -60, scale: 0.7 }}
          animate={{ opacity: 1, rotate: 0, scale: 1 }}
          exit={{ opacity: 0, rotate: 60, scale: 0.7 }}
          transition={{ duration: 0.16, ease: EASE_OUT }}
          className="inline-flex"
        >
          {theme === "dark" ? (
            <Moon aria-hidden="true" className="size-4" />
          ) : (
            <Sun aria-hidden="true" className="size-4" />
          )}
        </motion.span>
      </AnimatePresence>
    </button>
  );
}
