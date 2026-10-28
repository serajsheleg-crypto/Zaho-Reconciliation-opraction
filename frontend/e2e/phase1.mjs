// Whole-file viewer + database banner, end to end with screenshots:
//   node e2e/phase1.mjs <outDir> [baseUrl] [ourFile] [partnerFile]
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const out = process.argv[2] ?? "shots-phase1";
const base = process.argv[3] ?? "http://127.0.0.1:8077";
const dir = path.join(os.homedir(), "Desktop", "Recon Template Test Data", "01 OnePay");
const ours = process.argv[4] ?? path.join(dir, "Zaho export - OnePay 21-10-2026.xlsx");
const theirs = process.argv[5] ?? path.join(dir, "OnePay 21-10-2026.xlsx");
mkdirSync(out, { recursive: true });

const browser = await chromium.launch({ channel: "chrome", headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await ctx.addInitScript(() => {
  if (!localStorage.getItem("zr.lang")) localStorage.setItem("zr.lang", "en");
  if (!localStorage.getItem("zr.theme")) localStorage.setItem("zr.theme", "light");
});
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => m.type() === "error" && errors.push(`console: ${m.text()}`));
const shot = async (name, full = false) => {
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(out, `${name}.png`), fullPage: full });
  console.log("shot", name);
};
const check = (ok, what) => {
  if (!ok) throw new Error(`FAILED: ${what}`);
  console.log("ok:", what);
};

await page.goto(`${base}/login`);
await page.fill("#email", process.env.ZR_EMAIL ?? "admin@zaho.ly");
await page.fill("#password", process.env.ZR_PASSWORD ?? "Admin@2026");
await page.click("button[type=submit]");
await page.waitForURL(`${base}/`);
// Signing in applies the account's language; the checks below read English.
if ((await page.getAttribute("html", "lang")) === "ar") await page.getByRole("button", { name: "English" }).click();

// ---- upload: the card offers every row, not a 15-row preview
await page.goto(`${base}/new`);
await page.waitForSelector(".dropzone");
await page.locator(".ledger.a input[type=file]").setInputFiles(ours);
await page.locator(".ledger.b input[type=file]").setInputFiles(theirs);
await page.waitForSelector(".seam.joined", { timeout: 60000 });
await shot("p1-01-uploaded");
const viewA = page.locator(".ledger.a").getByRole("button", { name: /^View all .* rows$/ });
check((await viewA.textContent()).includes("64"), "button says all 64 rows");
await viewA.click();
await page.waitForSelector(".rows-view tbody tr");
check((await page.locator(".rows-view tbody tr").count()) === 64, "viewer lists all 64 rows on one page");
check((await page.locator(".rows-view thead th").count()) === 9, "line number + all 8 columns");
check((await page.locator(".rows-tools").textContent()).includes("All 64 rows and 8 columns were read"), "says the whole file was read");
await shot("p1-02-viewer-all");
await page.locator(".rows-view").evaluate((el) => (el.scrollTop = el.scrollHeight));
check((await page.locator(".rows-view tbody tr").last().locator("td").first().textContent()) === "65", "last row is line 65 of the sheet");
await shot("p1-03-viewer-bottom");
await page.fill(".rows-tools input[type=search], .rows-tools .input-group input", "top-up");
await page.waitForFunction(() => document.querySelector(".rows-tools")?.textContent?.includes("of 64 rows contain"));
await shot("p1-04-viewer-search");
await page.fill(".rows-tools .input-group input", "");
await page.waitForFunction(() => document.querySelectorAll(".rows-view tbody tr").length === 64);
await page.fill(".rows-tools input[type=number]", "40");
await page.keyboard.press("Enter");
await page.waitForSelector(".rows-view tbody tr.selected");
check((await page.locator(".rows-view tbody tr.selected td").first().textContent()) === "40", "go to line 40 highlights it");
await shot("p1-05-viewer-goto");
await page.keyboard.press("Escape");
await page.locator(".ledger.b").getByRole("button", { name: /^View all .* rows$/ }).click();
await page.waitForSelector(".rows-view tbody tr");
check((await page.locator(".rows-view thead th").count()) === 7, "OnePay: line + 6 columns");
await shot("p1-06-viewer-onepay");
await page.keyboard.press("Escape");

// ---- run it, then open the files from the run page
for (let i = 0; i < 3; i++) await page.getByRole("button", { name: "Continue", exact: true }).last().click().then(() => page.waitForTimeout(500));
await page.waitForSelector("#run-name");
await page.getByRole("button", { name: "Start reconciliation" }).click();
await page.waitForSelector(".hero-figure", { timeout: 120000 });
await shot("p1-07-run", false);
await page.locator(".run-files .btn").nth(1).click();
await page.waitForSelector(".rows-view tbody tr");
check((await page.locator(".rows-view tbody tr").count()) === 64, "run page opens OnePay's whole file");
await shot("p1-08-run-view-file");
await page.keyboard.press("Escape");
await page.locator("#results tbody tr").first().click();
await page.waitForSelector(".drawer .compare");
await shot("p1-09-record");
await page.keyboard.press("Escape");

// ---- System page lists recent problems
await page.goto(`${base}/system`);
await page.waitForSelector("h1");
await shot("p1-10-system", true);

// ---- Arabic, right to left
await page.evaluate(() => localStorage.setItem("zr.lang", "ar"));
await page.goto(`${base}/new`);
await page.waitForSelector(".dropzone");
await page.locator(".ledger.a input[type=file]").setInputFiles(ours);
await page.waitForSelector(".ledger.a .file-card");
await page.locator(".ledger.a").getByRole("button", { name: /عرض كل الأسطر/ }).click({ timeout: 60000 });
await page.waitForSelector(".rows-view tbody tr");
await shot("p1-11-viewer-ar");

await browser.close();
if (errors.length) {
  console.log("browser errors:\n" + errors.join("\n"));
  process.exitCode = 1;
}
