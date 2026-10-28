// Reconciliation templates, end to end in the real app, with screenshots:
//   node e2e/recon_templates.mjs <outDir> [baseUrl] [examplesDir]
// Needs the example templates saved first: python scripts/example_templates.py
// Creates a template through the editor, runs "Local cards — Dogoon" and "Transactions — Moamalat" on the
// example workbooks by picking the template and uploading two files (no column mapping), checks the counts,
// shows the error for files uploaded the wrong way round, and takes the Arabic screens.
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const out = process.argv[2] ?? "shots-recon";
const base = process.argv[3] ?? "http://127.0.0.1:8077";
const data = process.argv[4] ?? path.join(os.homedir(), "Desktop", "نماذج المطابقات امثلة");
mkdirSync(out, { recursive: true });

const browser = await chromium.launch({ channel: "chrome", headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
await ctx.addInitScript(() => {
  if (!localStorage.getItem("zr.lang")) localStorage.setItem("zr.lang", "en");
  localStorage.setItem("zr.theme", "light");
});
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => m.type() === "error" && !m.text().startsWith("Failed to load resource") && errors.push(`console: ${m.text()}`));
// Failed requests are reported with their address; the sign-in screen's "who am I?" before signing in is expected.
page.on("response", (r) => r.status() >= 400 && !r.url().endsWith("/api/auth/me") && errors.push(`${r.status()} ${r.url()}`));
const shot = async (name) => {
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(out, `${name}.png`), fullPage: true });
  console.log("shot", name);
};
const check = (ok, what) => {
  if (!ok) throw new Error(`FAILED: ${what}`);
  console.log("ok  ", what);
};

await page.goto(`${base}/login`);
await page.fill("#email", process.env.ZR_EMAIL ?? "admin@zaho.ly");
await page.fill("#password", process.env.ZR_PASSWORD ?? "Admin@2026");
await page.click("button[type=submit]");
await page.waitForURL(`${base}/`);
// The app keeps each person's language on their account: start in English whatever the last session used.
const setLang = (lang) =>
  page.evaluate(async (l) => {
    localStorage.setItem("zr.lang", l);
    await fetch("/api/auth/me", { method: "PATCH", headers: { "Content-Type": "application/json", "X-Requested-With": "zaho" }, body: JSON.stringify({ lang: l }) });
  }, lang);
await setLang("en");

// ---- the list
await page.goto(`${base}/recon-templates`);
await page.waitForSelector(".rt-card");
check((await page.locator(".rt-card").count()) >= 3, "three example templates are listed");
await shot("01-recon-templates");

// ---- the editor: a new template from two existing layouts
const uiName = `UI test ${Date.now() % 100000}`;
await page.goto(`${base}/recon-templates/new`);
await page.fill("#rt-name", uiName);
await page.selectOption("#rt-layout-a", { label: "Hawli - Local Cards" });
await page.selectOption("#rt-layout-b", { label: "Dogoon - Transactions" });
await page.waitForSelector(".pair-table");
check((await page.inputValue("#rt-label-b")) === "Dogoon", "the partner's name is filled from the layout's company");
const ticked = await page.locator(".pair-table input[type=checkbox]:checked").count();
check(ticked === 2, "amount and fee start ticked");
await shot("02-editor-new");
await page.click("button:has-text('Save template')");
await page.waitForURL(`${base}/recon-templates`);
check(await page.locator(".rt-card h2", { hasText: uiName }).waitFor().then(() => true, () => false), "the new template is saved and listed");

// ---- the field mapping: suggested from the two layouts, changed by hand, saved and read back
const mapName = `Mapping test ${Date.now() % 100000}`;
await page.goto(`${base}/recon-templates/new`);
await page.fill("#rt-name", mapName);
await page.selectOption("#rt-layout-a", { label: "Hawli - Khales" });
await page.selectOption("#rt-layout-b", { label: "Libyana - Khales sales" });
await page.waitForSelector(".pair-table.mapping");
const sel = (field, side) => page.locator(`.pair-table.mapping select[aria-label^="${field} · ${side}"]`);
const picked = async (field, side) => (await sel(field, side).inputValue()).slice(2);
check(await picked("Reference", "Zaho") === "ProviderReference" && await picked("Reference", "Libyana") === "Reference ID",
  "suggested matching key: ProviderReference ↔ Reference ID");
check(await picked("Amount", "Zaho") === "Amount" && await picked("Amount", "Libyana") === "Package value",
  "suggested amount: Amount ↔ Package value");
check(await picked("Fee", "Zaho") === "Fee" && await picked("Fee", "Libyana") === "Zaho commission",
  "suggested fee: Fee ↔ Zaho commission");
check(await picked("Status", "Libyana") === "", "Libyana has no status column: left unmapped, nothing invented");
await shot("02b-mapping-suggested");
await sel("Fee", "Libyana").selectOption("c:1% fee");
check(await page.locator(".map-reset").count() === 1, "the changed row is marked");
await page.click("button:has-text('Save template')");
await page.waitForURL(`${base}/recon-templates`);
const saved = (await page.evaluate(async () => (await fetch("/api/recon-templates")).json())).items.find((x) => x.name === mapName);
await page.goto(`${base}/recon-templates/${saved.id}`);
await page.waitForSelector(".pair-table.mapping");
check(await picked("Fee", "Libyana") === "1% fee", "the changed column is saved and shown again");
await shot("02c-mapping-reloaded");
await page.click("button:has-text('Delete')");
await page.click(".modal button.danger");
await page.waitForURL(`${base}/recon-templates`);

// ---- New reconciliation: the two ways
await page.goto(`${base}/new`);
await page.waitForSelector(".choose-grid");
await shot("03-new-choose");

async function runTemplate(name, fileA, fileB, shotName) {
  await page.goto(`${base}/new`);
  await page.click(`.tpl-option:has-text("${name}")`);
  await page.waitForSelector(".tpl-chosen");
  await page.locator(".ledger.a input[type=file]").setInputFiles(path.join(data, fileA));
  await page.locator(".ledger.b input[type=file]").setInputFiles(path.join(data, fileB));
  await page.waitForSelector(".notice.good, .notice.bad", { timeout: 60000 });
  await shot(shotName);
}

// ---- Local cards: fee read from Dogoon's profit_customer row
await runTemplate("Local cards — Dogoon", "كروت محلية-حولي.xlsx", "كروت محلية-دوجون.xlsx", "04-run-cards-ready");
check(await page.locator(".notice.good").isVisible(), "the local card files fit the template");
await page.click("button:has-text('Run reconciliation')");
await page.waitForURL(/\/runs\/\d+$/);
await page.waitForSelector("text=Template: Local cards", { timeout: 60000 });
await page.waitForFunction(() => !document.querySelector(".progress, .run-progress"), null, { timeout: 60000 }).catch(() => undefined);
await page.waitForTimeout(1500);
await shot("05-run-cards-result");
const runId = page.url().split("/").pop();
const run = await page.evaluate(async (id) => (await fetch(`/api/runs/${id}`)).json(), runId);
check(run.status === "completed" && run.matched === 5 && run.exceptions === 0, `local cards: 5 matched, 0 differences (got ${run.matched}/${run.exceptions})`);
check(run.summary.compared.fee === true && run.summary.compared.date === false, "fee compared, date not compared");

// ---- Wallet top-ups, files the wrong way round: refused with a clear message
await runTemplate("Transactions — Moamalat", "معاملات-المزود.xlsx", "معاملات-حولي.xlsx", "06-run-topups-swapped");
check(await page.locator(".notice.bad", { hasText: "wrong way round" }).waitFor({ timeout: 10000 }).then(() => true, () => false), "swapped files are refused");
check(await page.locator("button:has-text('Run reconciliation')").isDisabled(), "the run button stays off");

// ---- another service's Hawli report: same columns, but no row of the expected service -> refused
await runTemplate("Khales — Libyana", "كروت محلية-حولي.xlsx", "خالص-ليبيانا.xlsx", "06b-run-khales-wrong-hawli");
check(await page.locator(".notice.bad", { hasText: "ServiceCode = LocalKhales" }).waitFor({ timeout: 10000 }).then(() => true, () => false),
  "the Local Cards Hawli report is refused by the Khales template");
check(await page.locator("button:has-text('Run reconciliation')").isDisabled(), "the run button stays off for the wrong report");

// ---- and the right way round
await runTemplate("Transactions — Moamalat", "معاملات-حولي.xlsx", "معاملات-المزود.xlsx", "07-run-topups-ready");
await page.click("button:has-text('Run reconciliation')");
await page.waitForURL(/\/runs\/\d+$/);
await page.waitForTimeout(3000);
const run2 = await page.evaluate(async (id) => (await fetch(`/api/runs/${id}`)).json(), page.url().split("/").pop());
check(run2.status === "completed" && run2.matched === 7, `top-ups: 7 matched (got ${run2.matched})`);
await shot("08-run-topups-result");

// ---- Khales: the example files hold different transactions, so 4 exceptions; they open on their own
await runTemplate("Khales — Libyana", "خالص-حولي.xlsx", "خالص-ليبيانا.xlsx", "07b-run-khales-ready");
check(await page.locator(".notice.warn", { hasText: "no reference in common" }).isVisible(), "no shared reference is warned about");
await page.click("button:has-text('Run reconciliation')");
await page.waitForURL(/\/runs\/\d+$/);
const khalesId = page.url().split("/").pop();
let khales;
for (let i = 0; i < 40; i++) {
  khales = await page.evaluate(async (id) => (await fetch(`/api/runs/${id}`)).json(), khalesId);
  if (khales.status === "completed") break;
  await page.waitForTimeout(500);
}
check(khales.exceptions === 4, `khales: 4 exceptions (got ${khales.exceptions})`);
await page.goto(`${base}/runs`);
await page.click(`a[href="/exceptions?run=${khalesId}&review=all"]`);
await page.waitForURL(new RegExp(`/exceptions\\?run=${khalesId}`));
await page.waitForSelector(".run-scope h2:not(:has-text('…'))");
await page.waitForTimeout(800);
const rows = page.locator("table.data tbody tr");
check((await rows.count()) === 4, `the run's "Open exceptions" shows exactly its 4 exceptions (got ${await rows.count()})`);
const runCells = await page.locator("table.data tbody tr td.cut a").allTextContents();
check(runCells.every((x) => x === `#${khalesId}`), "every row belongs to that run");
await shot("08b-exceptions-one-run");
await page.click("button:has-text('Show all reconciliations')");
await page.waitForSelector(".run-group");
check(await page.locator(`.run-group:has-text("#${khalesId}")`).isVisible(), "the run is listed under By reconciliation");
await shot("08c-exceptions-by-run");

// ---- the manual way is still there
await page.goto(`${base}/new?manual=1`);
await page.waitForSelector(".steps");
check(await page.locator(".ledger").count() === 2, "manual setup still shows Siraj's two-sided upload");
await shot("09-manual");

// ---- clean up the UI test template
const list = await page.evaluate(async () => (await fetch("/api/recon-templates")).json());
const mine = list.items.find((x) => x.name === uiName);
await page.goto(`${base}/recon-templates/${mine.id}`);
await page.click("button:has-text('Delete')");
await page.click(".modal button.danger");
await page.waitForURL(`${base}/recon-templates`);
await page.waitForSelector(".rt-card");
check(!(await page.locator(".rt-card h2", { hasText: uiName }).count()), "the UI test template is deleted");

// ---- Arabic
await page.evaluate(() => localStorage.setItem("zr.lang", "ar"));
await page.goto(`${base}/new`);
await page.waitForSelector(".choose-grid");
await shot("10-ar-new-choose");
await page.goto(`${base}/recon-templates`);
await page.waitForSelector(".rt-card");
await shot("11-ar-recon-templates");
await page.goto(`${base}/recon-templates/${list.items.find((x) => x.name.startsWith("Local cards")).id}`);
await page.waitForSelector(".pair-table");
await shot("12-ar-editor");

await setLang("en");
await browser.close();
if (errors.length) {
  console.log(errors.join("\n"));
  process.exit(1);
}
console.log("all good");
