import { expect, test } from "@playwright/test";

const destinations = [
  ["استراتژی و بازار", "strategy-market"], ["موقعیت‌ها و سفارش‌ها", "positions-orders"],
  ["بک‌تست و تحلیل", "backtest-analytics"], ["کنترل ربات", "robot-control"],
  ["هشدارها و رویدادها", "alerts-events"], ["اتصال و تنظیمات", "connections"],
] as const;

test("all nine routes keep RTL themes navigation and meaningful unavailable controls", async ({ page }) => {
  const errors: string[] = [], requests: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("request", request => { if (request.method() !== "GET" || !request.url().startsWith("http://127.0.0.1:5173/")) requests.push(request.url()); });
  await page.goto("/?view=demo");
  await expect(page.getByText("به‌زودی", { exact: true })).toHaveCount(0);
  for (const [name, hash] of destinations) {
    await page.getByRole("link", { name, exact: true }).click();
    await expect(page).toHaveURL(new RegExp("#" + hash + "$"));
    await expect(page.getByRole("heading", { name, exact: true, level: 1 })).toBeVisible();
    await expect(page.getByRole("link", { name, exact: true })).toHaveAttribute("aria-current", "page");
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    await expect(page.locator(".trade-disabled")).toBeDisabled();
    await expect(page.locator(".operation-safety")).toContainText("executable_ready = false");
  }
  await page.goto("/?view=demo#robot-control");
  for (const name of ["شروع worker — در دسترس نیست", "توقف worker — در دسترس نیست", "Refresh زنده — در دسترس نیست"]) {
    await expect(page.getByRole("button", { name, exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name, exact: true })).toHaveAttribute("title", /.+/);
  }
  await page.getByRole("button", { name: "پوسته روشن", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  for (const width of [390, 760, 1366]) {
    await page.setViewportSize({ width, height: 900 });
    for (const [, hash] of destinations) {
      await page.goto("/?view=demo#" + hash);
      const overflow = await page.evaluate(() => [...document.querySelectorAll(".workspace *")].filter(e => { const r=e.getBoundingClientRect(); return r.left < -1 || r.right > innerWidth + 1; }).map(e => e.className).slice(0, 20));
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${width} ${hash}: ${overflow}`).toBe(true);
    }
  }
  expect(errors).toEqual([]);
  expect(requests).toEqual([]);
});

test("local redacted reports populate public/account views without network secrets or invented trading state", async ({ page }) => {
  const requests: string[] = [];
  page.on("request", request => { if (request.method() !== "GET" || !request.url().startsWith("http://127.0.0.1:5173/")) requests.push(request.url()); });
  await page.goto("/?view=demo#connections");
  const publicReport = { mode: "PUBLIC_OBSERVATION_ONLY", status: "OBSERVED_BLOCKED", symbol: "BTCTMN", health: "RUNNING",
    action: "NO_TRADE", executable_ready: false, observed_at: 1792757067, decision_cutoff: 1792756800,
    orderbook: { best_bid: "99", best_ask: "101", spread_pct: "2" }, readiness_blockers: ["PE_UNAVAILABLE"], observation_failures: [],
    pipeline: { validation: { h1_candles: 264, m1_m15: { complete_m15: 24 } }, summary: { evaluated_steps: 4, technical_eligible_steps: 0, replay_errors: 0 },
      evaluated_steps: [{ technical_evidence: { trend: { eligible: true, reasons: ["TREND_CONDITIONS_MET"] }, pullback: { eligible: false, reasons: ["CURRENT_CLOSE_NOT_ABOVE_EMA20"] } } }] } };
  const upload = async (value: unknown) => page.getByLabel("بارگذاری گزارش سانسورشده").setInputFiles({ name: "synthetic-redacted-test.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(value)) });
  await upload(publicReport);
  await expect(page.getByRole("status")).toContainText("فقط در حافظه");
  await page.getByRole("link", { name: "استراتژی و بازار", exact: true }).click();
  await expect(page.getByRole("region", { name: "مظنه و عمق مشاهده‌شده" })).toContainText("99");
  await expect(page.getByRole("region", { name: "استراتژی و بازار · TP-1" })).toContainText("264 / 24");
  await page.getByRole("link", { name: "اتصال و تنظیمات", exact: true }).click();
  await upload({ mode: "HACKATHON_SPOT_READ_PREFLIGHT", action: "NO_TRADE", executable_ready: false,
    authenticated_reads_succeeded: true, discovery_identity_matched: true, quote_available_positive: true,
    base_exposure_present: false, other_asset_exposure_present: false, open_orders_observed: 0,
    order_coverage: "UNKNOWN_PAGINATION_METADATA", blockers: ["ORDER_COVERAGE_UNVERIFIED"], endpoints: { "/v1/account/openOrders": 200 } });
  await page.getByRole("link", { name: "موقعیت‌ها و سفارش‌ها", exact: true }).click();
  await expect(page.getByRole("region", { name: "محدودیت‌های حساب" })).toContainText("ORDER_COVERAGE_UNVERIFIED");
  await expect(page.getByRole("region", { name: "موقعیت‌ها و سفارش‌ها", exact: true })).toContainText("صفر مشاهده‌شده به معنی حساب flat");
  await page.getByRole("link", { name: "اتصال و تنظیمات", exact: true }).click();
  for (const bad of [{ ...publicReport, headers: { "x-api-key": "FAKE_SECRET_MUST_NOT_RENDER" } }, { ...publicReport, action: "BUY", executable_ready: true }]) {
    await upload(bad);
    await expect(page.getByRole("status")).toContainText("فایل رد شد");
    await expect(page.locator("body")).not.toContainText("FAKE_SECRET_MUST_NOT_RENDER");
  }
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain("BTCTMN");
  await page.getByRole("button", { name: "پاک‌کردن گزارش‌های محلی", exact: true }).click();
  await expect(page.getByRole("button", { name: "پاک‌کردن گزارش‌های محلی", exact: true })).toBeDisabled();
  await page.reload();
  await expect(page.getByRole("region", { name: "منبع و محدودیت شواهد" })).toContainText("بارگذاری نشده");
  expect(requests).toEqual([]);
});
