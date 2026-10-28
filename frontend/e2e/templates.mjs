// Company templates, end to end in the real app, with screenshots:
//   node e2e/templates.mjs <outDir> [baseUrl] [testDataDir]
// Creates OnePay and Ufuq Wallet (and our Zaho template) from sample files through the UI, then reconciles the
// next day's files with the templates and checks every count against the test data's expected.json.
// Make the test data first: python scripts/make_template_samples.py
import { chromium } from "playwright";
import { mkdirSync, readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const out = process.argv[2] ?? "shots-templates";
const base = process.argv[3] ?? "http://127.0.0.1:8077";
const data = process.argv[4] ?? path.join(os.homedir(), "Desktop", "Recon Template Test Data");
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
const companySection = (name) => page.locator("section.company", { has: page.locator("h2", { hasText: name }) });

await page.goto(`${base}/login`);
await page.fill("#email", process.env.ZR_EMAIL ?? "admin@zaho.ly");
await page.fill("#password", process.env.ZR_PASSWORD ?? "Admin@2026");
await page.click("button[type=submit]");
await page.waitForURL(`${base}/`);

// ---- companies
await page.goto(`${base}/companies`);
await page.waitForSelector("h1");
await shot("t01-companies-start");
for (const name of ["OnePay", "Ufuq Wallet"]) {
  if (await companySection(name).count()) continue;
  await page.getByRole("button", { name: "Add company" }).click();
  await page.fill("#co-name", name);
  await shot(`t02-company-form-${name.replace(/\s/g, "")}`, false);
  await page.getByRole("button", { name: "Save changes" }).click();
  await companySection(name).waitFor();
}

/** New template from a sample file; `extra` = [column, name] compared by name, `sheet` = the data sheet. */
async function addTemplate({ company, name, file, extra, sheet, shotName }) {
  if (await companySection(company).getByRole("link", { name, exact: true }).count()) return;
  await companySection(company).getByRole("link", { name: "Add template" }).click();
  await page.waitForSelector("#tp-name");
  await page.fill("#tp-name", name);
  await page.locator(".ledger input[type=file]").setInputFiles(file);
  await page.waitForSelector(".tpl-row select", { timeout: 60000 });
  if (sheet) {
    await page.locator(".ledger .field select").selectOption(sheet);
    await page.waitForFunction((s) => document.querySelector(".ledger-title span")?.textContent?.includes(s), sheet, { timeout: 60000 });
    await page.waitForSelector(".tpl-row select");
  }
  if (extra) {
    const [column, label] = extra;
    await page.getByRole("button", { name: "Add field" }).click();
    const row = page.locator(".panel", { has: page.locator("h2", { hasText: "Other fields to compare" }) }).locator(".row").last();
    await row.locator("select").selectOption(column);
    await row.locator("input").fill(label);
  }
  await shot(shotName);
  await page.getByRole("button", { name: "Save template" }).first().click();
  await page.waitForURL(`${base}/companies`);
  await companySection(company).getByRole("link", { name, exact: true }).waitFor();
}

await addTemplate({
  company: "Zaho", name: "System export", extra: ["Direction", "Direction"], shotName: "t03-template-zaho",
  file: path.join(data, "01 OnePay", "Zaho export - OnePay 20-10-2026.xlsx"),
});
await addTemplate({
  company: "OnePay", name: "Daily transactions", extra: ["direction", "Direction"], shotName: "t04-template-onepay",
  file: path.join(data, "01 OnePay", "OnePay 20-10-2026.xlsx"),
});
const timeValue = await page.evaluate(async () => {
  const list = await (await fetch("/api/companies")).json();
  const tpl = list.items.find((c) => c.name === "OnePay").templates[0];
  return tpl.summary.fields.time;
});
if (timeValue !== "time") throw new Error(`OnePay template has no time column: ${timeValue}`);
await addTemplate({
  company: "Ufuq Wallet", name: "Transactions report", sheet: "Transactions", shotName: "t05-template-ufuq",
  file: path.join(data, "04 Ufuq Wallet", "Ufuq Wallet 20-10-2026.xlsx"),
});
await shot("t06-companies");

// ---- reconcile the next day's files with the templates
async function reconcile({ folder, ours, theirs, tplA, tplB, label, prefix }) {
  await page.goto(`${base}/new?manual=1`);
  await page.waitForSelector("#tpl-a optgroup", { state: "attached" });
  await page.locator("#tpl-a").selectOption({ label: tplA });
  await page.locator("#tpl-b").selectOption({ label: tplB });
  if ((await page.inputValue("#label-b")) !== label) throw new Error(`label not taken from the company: ${await page.inputValue("#label-b")}`);
  await page.locator(".ledger.a input[type=file]").setInputFiles(path.join(data, folder, ours));
  await page.locator(".ledger.b input[type=file]").setInputFiles(path.join(data, folder, theirs));
  await page.waitForSelector(".seam.joined", { timeout: 90000 });
  await shot(`${prefix}-sources`);
  await page.getByRole("button", { name: "Continue", exact: true }).last().click();
  await page.waitForSelector(".tpl-line");
  await shot(`${prefix}-fields`);
  await page.getByRole("button", { name: "Continue", exact: true }).last().click();
  await page.getByRole("button", { name: "Continue", exact: true }).last().click();
  await page.waitForSelector("text=Nothing stops the run.", { timeout: 60000 });
  await shot(`${prefix}-check`);
  await page.getByRole("button", { name: "Start reconciliation" }).click();
  await page.waitForURL(/\/runs\/\d+$/);
  const id = Number(page.url().split("/").pop());
  const run = await page.evaluate(async (rid) => {
    for (let i = 0; i < 240; i++) {
      const r = await (await fetch(`/api/runs/${rid}`)).json();
      if (["completed", "failed", "cancelled"].includes(r.status)) return r;
      await new Promise((ok) => setTimeout(ok, 500));
    }
    return null;
  }, id);
  await page.reload();
  await page.waitForSelector("h1");
  await shot(`${prefix}-result`);
  const day = JSON.parse(readFileSync(path.join(data, folder, "expected.json"), "utf-8")).find((x) => x.their_file === theirs);
  const got = Object.fromEntries(Object.entries(run.summary.categories).filter(([, v]) => v.count).map(([k, v]) => [k, v.count]));
  const same = JSON.stringify(Object.entries(got).sort()) === JSON.stringify(Object.entries(day.categories).sort());
  console.log(`${folder}: run #${id} ${run.status}`, same ? "matches expected.json" : `DIFFERS\n got ${JSON.stringify(got)}\n exp ${JSON.stringify(day.categories)}`);
  if (!same) throw new Error(`${folder} counts differ from expected.json`);
}

await reconcile({
  folder: "01 OnePay", ours: "Zaho export - OnePay 21-10-2026.xlsx", theirs: "OnePay 21-10-2026.xlsx",
  tplA: "System export", tplB: "Daily transactions", label: "OnePay", prefix: "t07-onepay",
});
await reconcile({
  folder: "04 Ufuq Wallet", ours: "Zaho export - Ufuq Wallet 21-10-2026.xlsx", theirs: "Ufuq Wallet 21-10-2026.xlsx",
  tplA: "System export", tplB: "Transactions report", label: "Ufuq Wallet", prefix: "t08-ufuq",
});

// ---- Arabic, and a phone-width check of the companies page
await page.evaluate(() => localStorage.setItem("zr.lang", "ar"));
await page.goto(`${base}/companies`);
await page.waitForSelector("section.company");
await shot("t09-companies-ar");
await companySection("OnePay").getByRole("link", { name: "Daily transactions", exact: true }).click();
await page.waitForSelector(".tpl-row select");
await shot("t10-template-onepay-ar");
await page.evaluate(() => localStorage.setItem("zr.lang", "en"));
await page.setViewportSize({ width: 390, height: 844 });
await page.goto(`${base}/companies`);
await page.waitForSelector("section.company");
await shot("t11-companies-phone");
const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
console.log("phone horizontal overflow px:", overflow);

await browser.close();
if (errors.length) {
  console.log("BROWSER ERRORS:\n" + errors.join("\n"));
  process.exit(1);
}
console.log("ok");
