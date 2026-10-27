// Flat config for issue #22's `npm run lint` gate: the recommended
// typescript-eslint ruleset (without type-aware rules, so `lint` needs no
// project-service pass) plus the core JS recommendations. Console use is
// intentional in this repo (renderer fallback diagnostics, the
// report-an-issue affordance), so no-console stays off; explicit `any`
// stays off because row payloads are genuinely open (DataTable rows are
// `dict[str, Any]` server-side). Build output, deps and generated files
// are ignored.
import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: ["dist/**", "node_modules/**", "coverage/**", "*.config.js"],
  },
  js.configs.recommended,
  // Node runtime script (screenshot capture): console/process are globals.
  {
    files: ["scripts/**/*.mjs"],
    languageOptions: { globals: globals.node },
  },
  ...tseslint.configs.recommended,
  {
    rules: {
      "no-console": "off",
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
);
