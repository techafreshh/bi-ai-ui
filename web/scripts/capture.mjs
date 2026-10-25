/**
 * README screenshot capture: drives the real UI (dev server on :5173) through
 * the three golden paths + the decision log, saving to ../docs/*.png.
 *
 * Usage: stack up via `docker compose up`, then `npm run screenshots`.
 * Requires Playwright chromium on the host: `npx playwright install chromium`.
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const WEB_URL = process.env.WEB_URL ?? "http://localhost:5173";
const OUT_DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "docs");

const QUERY_SELECT_TIMEOUT = 90_000;

// Wait until the canvas shows the assembled view for a query.
async function waitForCanvas(page, marker, timeout = QUERY_SELECT_TIMEOUT) {
  await page.getByText(marker, { exact: false }).first().waitFor({ timeout });
}

async function ask(page, query) {
  const input = page.getByPlaceholder("Type a message…");
  await input.waitFor({ state: "visible", timeout: 30_000 });
  await input.click();
  await input.fill(query);
  await input.press("Enter");
}

async function freshPage(browser) {
  const context = await browser.newContext({
    viewport: { width: 1600, height: 1000 },
    deviceScaleFactor: 2,
  });
  const page = await context.newPage();
  await page.goto(WEB_URL, { waitUntil: "networkidle" });
  await page.getByPlaceholder("Type a message…").waitFor({ state: "visible", timeout: 30_000 });
  return { context, page };
}

const CAPTURES = [
  {
    file: "screenshot-q3-dashboard.png",
    query: "Show me sales in Q3",
    marker: "Net-new ARR (Q3)",
  },
  {
    file: "screenshot-churn-drilldown.png",
    query: "Why did churn spike in August?",
    marker: "Investigate",
  },
  {
    file: "screenshot-data-quality.png",
    query: "Is the data clean?",
    marker: "duplicates",
  },
];

async function main() {
  mkdirSync(OUT_DIR, { recursive: true });
  const browser = await chromium.launch();
  try {
    for (const capture of CAPTURES) {
      const { context, page } = await freshPage(browser);
      await ask(page, capture.query);
      await waitForCanvas(page, capture.marker);
      await page.waitForTimeout(1200); // let narrative text settle
      await page.screenshot({ path: join(OUT_DIR, capture.file), fullPage: false });
      console.log(`captured ${capture.file}`);
      await context.close();
    }

    // Decision-log drawer over the DQ path (noul p>0.7 makes the log talk).
    const { context, page } = await freshPage(browser);
    await ask(page, "Is the data clean?");
    await waitForCanvas(page, "duplicates");
    await page.getByRole("button", { name: "Decision log" }).click();
    await page.getByText("typed decisions").waitFor({ timeout: 15_000 });
    await page.waitForTimeout(600);
    await page.screenshot({ path: join(OUT_DIR, "screenshot-decision-log.png") });
    console.log("captured screenshot-decision-log.png");
    await context.close();
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error("capture failed:", error);
  process.exit(1);
});
