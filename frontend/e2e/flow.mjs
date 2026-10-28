// End-to-end walk through the real app (served by FastAPI) with screenshots of every screen.
//   node e2e/flow.mjs <outDir> [baseUrl]
// Uses the installed Google Chrome (no Playwright browser download needed).
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const out = process.argv[2] ?? "shots";
const base = process.argv[3] ?? "http://127.0.0.1:8077";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const samples = path.join(root, "samples");
mkdirSync(out, { recursive: true });

const browser = await chromium.launch({ channel: "chrome", headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
await ctx.addInitScript(() => {
  if (!localStorage.getItem("zr.lang")) localStorage.setItem("zr.lang", "en");
  if (!localStorage.getItem("zr.theme")) localStorage.setItem("zr.theme", "light");
});
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => m.type() === "error" && errors.push(`console: ${m.text()}`));
const shot = async (name, full = true) => {
  await page.waitForTimeout(450);
  await page.screenshot({ path: path.join(out, `${name}.png`), fullPage: full });
  console.log("shot", name);
};

await page.goto(`${base}/login`);
await shot("01-login", false);
await page.fill("#email", process.env.ZR_EMAIL ?? "admin@zaho.ly");
await page.fill("#password", process.env.ZR_PASSWORD ?? "Admin@2026");
await page.click("button[type=submit]");
await page.waitForURL(`${base}/`);
await page.waitForSelector("h1");
await shot("02-overview");

// Services & statuses: one service without fee reconciliation, and the partner's status words approved.
await page.goto(`${base}/settings`);
await page.waitForSelector("h1");
await shot("02b-settings-empty");
if (!(await page.getByText("Wallet top-up").count())) {
  await page.getByRole("button", { name: "Add service" }).click();
  await page.fill("#svc-name", "Wallet top-up");
  await page.locator('input[name="svc-revenue"]').nth(1).check({ force: true });
  await shot("02c-service-form", false);
  await page.getByRole("button", { name: "Save changes" }).click();
  await page.waitForSelector("text=Wallet top-up");
}
for (const [label, meaning] of [["Approved", "SUCCESS"], ["Declined", "FAILED"], ["In Progress", "PENDING"]]) {
  if (await page.locator("td.raw", { hasText: new RegExp(`^${label}$`) }).count()) continue;
  await page.getByRole("button", { name: "Add status name" }).click();
  await page.fill("#st-label", label);
  await page.selectOption("#st-meaning", meaning);
  await page.getByRole("button", { name: "Save changes" }).click();
  await page.locator("td.raw", { hasText: new RegExp(`^${label}$`) }).waitFor();
}
await shot("02d-settings");

await page.goto(`${base}/new`);
await page.waitForSelector(".dropzone");
await shot("03-new-empty", false);
// ZR_OURS / ZR_PARTNER may be absolute paths to other files.
await page.locator(".ledger.a input[type=file]").setInputFiles(path.resolve(samples, process.env.ZR_OURS ?? "zaho_transactions.csv"));
await page.locator(".ledger.b input[type=file]").setInputFiles(path.resolve(samples, process.env.ZR_PARTNER ?? "partner_settlement.xlsx"));
await page.waitForSelector(".seam.joined", { timeout: 60000 });
await shot("04-new-sources");
// Every row of the file, not a preview: page 2 and a search.
await page.getByRole("button", { name: /^View all .* rows$/ }).first().click();
await page.waitForSelector(".rows-view tbody tr");
await shot("04b-whole-file", false);
await page.keyboard.press("Escape");
await page.getByRole("button", { name: "Continue", exact: true }).last().click();
await page.waitForSelector(".pairs");
await shot("05-new-fields");
await page.getByRole("button", { name: "Fix detection" }).click();
await page.waitForSelector(".pairs select");
await shot("05b-new-fields-fix");
await page.getByRole("button", { name: "Done", exact: true }).click();
await page.getByRole("button", { name: "Continue", exact: true }).last().click();
await page.waitForSelector("#service option:nth-child(2)", { state: "attached" });
await page.locator("#service").selectOption({ index: 1 });
await shot("06-new-rules");
await page.getByRole("button", { name: "Continue", exact: true }).last().click();
await page.waitForSelector("#run-name");
await page.getByText("Nothing stops the run.").waitFor({ timeout: 60000 });
await shot("06b-new-check");
await page.getByRole("button", { name: "Start reconciliation" }).click();
await page.waitForURL(/\/runs\/\d+$/);
await page.waitForSelector(".hero-figure", { timeout: 120000 });
await shot("07-run");
const runUrl = page.url();

await page.locator("#results tbody tr").first().click();
await page.waitForSelector(".drawer .compare");
await shot("08-drawer", false);
await page.keyboard.press("Escape");

await page.getByRole("button", { name: /download results/i }).click();
await shot("09-export-menu", false);
const [download] = await Promise.all([
  page.waitForEvent("download", { timeout: 120000 }),
  page.getByRole("button", { name: /excel workbook/i }).click(),
]);
await download.saveAs(path.join(out, download.suggestedFilename()));
console.log("downloaded", download.suggestedFilename());
await page.keyboard.press("Escape");

await page.goto(`${base}/exceptions`);
await page.waitForSelector("table.data tbody tr");
await shot("10-exceptions");
await page.goto(`${base}/runs`);
await page.waitForSelector("table.data tbody tr");
await shot("11-runs", false);
await page.goto(`${base}/`);
await page.waitForSelector(".hero-figure");
await shot("14-overview-after");

// Arabic + dark theme
await page.evaluate(() => {
  localStorage.setItem("zr.lang", "ar");
  localStorage.setItem("zr.theme", "dark");
});
await page.goto(runUrl);
await page.waitForSelector(".hero-figure");
await shot("15-run-ar-dark");
await page.locator("#results tbody tr").nth(2).click();
await page.waitForSelector(".drawer .compare");
await shot("16-drawer-ar-dark", false);
await page.keyboard.press("Escape");
await page.goto(`${base}/new`);
await page.waitForSelector(".dropzone");
await shot("17-new-ar-dark", false);
await page.goto(`${base}/settings`);
await page.waitForSelector("td.raw");
await shot("17b-settings-ar-dark");

// Phone width, Arabic light
await page.evaluate(() => localStorage.setItem("zr.theme", "light"));
await page.setViewportSize({ width: 390, height: 844 });
await page.goto(runUrl);
await page.waitForSelector(".hero-figure");
await shot("18-run-phone-ar");
await page.evaluate(() => localStorage.setItem("zr.lang", "en"));
await page.goto(`${base}/new`);
await page.waitForSelector(".dropzone");
await shot("19-new-phone-en");

console.log("errors", JSON.stringify(errors, null, 1));
await browser.close();
