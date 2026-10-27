// Screenshot capture for the README demo (issue #22).
//
// Drives the RUNNING stack (docker compose up, or the dev servers on the
// same ports) with playwright-core over the system chromium — no browser
// download, so the Dockerfile stays lean. Each golden query gets a fresh
// page (fresh agent thread): type into the CopilotSidebar prompt input,
// submit with Enter, wait for the canvas to stream the expected layout,
// then screenshot into docs/. The drawer shot opens the Decision log on
// the data-quality run.
//
//   WEB_URL=http://localhost:5173 CHROMIUM_PATH=/usr/bin/chromium \
//     node scripts/capture.mjs
//
// Exits non-zero when any canvas never reaches its expected state, so a
// half-rendered docs/ batch can never be committed silently.

import { statSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const WEB_URL = process.env.WEB_URL ?? "http://localhost:5173";
const DOCS_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "docs");
const CHROMIUM_CANDIDATES = [
  process.env.CHROMIUM_PATH,
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
  "/usr/bin/google-chrome",
].filter(Boolean);

function findChromium() {
  for (const candidate of CHROMIUM_CANDIDATES) {
    try {
      if (statSync(candidate).isFile()) return candidate;
    } catch {
      // try the next candidate
    }
  }
  return undefined; // playwright-core falls back to its own channel lookup
}

async function sendQuery(page, query, waitFor) {
  const input = page.getByPlaceholder("Type a message…");
  await input.waitFor({ timeout: 30_000 });
  await input.fill(query);
  await input.press("Enter");
  // The user bubble echoes the query once the run is accepted...
  await page
    .getByTestId("dashboard-canvas")
    .getByText(waitFor, { exact: false })
    .first()
    .waitFor({ timeout: 180_000 });
}

async function capture(query, waitFor, file, { drawer = false } = {}) {
  const executablePath = findChromium();
  const browser = await chromium.launch({
    ...(executablePath ? { executablePath } : {}),
    args: ["--no-sandbox"],
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
    await page.goto(WEB_URL, { waitUntil: "networkidle", timeout: 60_000 });
    await page.getByTestId("dashboard-canvas").waitFor({ timeout: 30_000 });
    await sendQuery(page, query, waitFor);
    // Let number animations and chart transitions settle before the shot.
    await page.waitForTimeout(2500);
    if (drawer) {
      await page.getByRole("button", { name: "Decision log" }).click();
      await page.getByTestId("decision-drawer").waitFor({ timeout: 15_000 });
      await page.waitForTimeout(800);
    }
    const out = resolve(DOCS_DIR, file);
    await page.screenshot({ path: out, fullPage: !drawer });
    console.log(`captured ${out}`);
  } finally {
    await browser.close();
  }
}

await mkdir(DOCS_DIR, { recursive: true });
await capture("Show me sales in Q3", "Net-new ARR", "screenshot-q3-dashboard.png");
await capture(
  "Why did churn spike in August?",
  "Investigate",
  "screenshot-churn-drilldown.png",
);
await capture(
  "Is the data clean?",
  "Report an issue",
  "screenshot-data-quality.png",
);
await capture("Is the data clean?", "Report an issue", "screenshot-decision-log.png", {
  drawer: true,
});
