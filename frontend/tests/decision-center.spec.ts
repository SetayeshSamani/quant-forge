import { expect, test } from "@playwright/test";

test("decision flow and journal remain read-only synthetic NO_TRADE", async ({
  page,
}) => {
  const errors: string[] = [];
  const unexpectedRequests: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (
      request.method() !== "GET" ||
      !request.url().startsWith("http://127.0.0.1:5173/")
    )
      unexpectedRequests.push(request.method() + " " + request.url());
  });
  await page.goto("/");
  await page.getByRole("link", { name: "مرکز تصمیم", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "مرکز تصمیم", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "مرکز تصمیم", exact: true }),
  ).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("note")).toContainText("داده‌های نمایشی");
  await expect(page.locator(".decision-outcome")).toContainText("NO_TRADE");
  await expect(page.locator(".decision-outcome")).toContainText(
    "تأیید اجرا: ندارد",
  );
  await expect(page.locator(".stage-card h3")).toHaveText([
    "داده بازار",
    "استراتژی",
    "ریسک",
    "اعتبارسنجی",
    "ثبت رویداد",
    "اجرا",
  ]);
  for (const label of [
    "داده بازار",
    "استراتژی",
    "ریسک",
    "اعتبارسنجی",
    "ثبت رویداد",
    "اجرا",
  ]) {
    const button = page.getByRole("button", {
      name: `جزئیات مرحله ${label}`,
      exact: true,
    });
    await button.click();
    await expect(button).toHaveAttribute("aria-pressed", "true");
    const detail = page.getByRole("region", {
      name: `جزئیات ${label}`,
      exact: true,
    });
    await detail.locator("summary").click();
    const evidence = JSON.parse(await detail.locator("pre").innerText());
    expect(evidence.source).toBe("SYNTHETIC_UI_FIXTURE");
    expect(evidence.action).toBe("NO_TRADE");
    expect(evidence.executable_ready).toBe(false);
    expect(evidence.execution_approved).toBe(false);
    expect(evidence.reasons.length).toBeGreaterThan(0);
    expect(evidence).not.toHaveProperty("quantity");
  }
  await expect(page.locator(".trade-disabled")).toBeDisabled();
  await expect(page.locator(".candidate-disclaimer")).toContainText(
    "کاندیدای فنی ≠ تأیید اجرایی",
  );
  await expect(page.locator(".decision-journal tbody tr")).toHaveCount(3);
  await page.getByRole("button", { name: "مشاهده همه", exact: true }).click();
  await expect(page.locator(".decision-journal tbody tr")).toHaveCount(6);
  await page
    .getByRole("button", {
      name: "مشاهده جزئیات رویداد اعتبارسنجی",
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("region", { name: "جزئیات اعتبارسنجی", exact: true }),
  ).toContainText("MISSING_CREDIT_PLAN_EVIDENCE");
  await page.goBack();
  await expect(
    page.getByRole("heading", { name: "نمای کلی", exact: true }),
  ).toBeVisible();
  expect(errors).toEqual([]);
  expect(unexpectedRequests).toEqual([]);
});

test("decision deep link, themes and responsive navigation reuse the shell", async ({
  page,
}) => {
  await page.goto("/#decision-center");
  await expect(page).toHaveTitle("مرکز تصمیم | QuantForge DEMO");
  const dark = await page
    .locator("body")
    .evaluate((element) => getComputedStyle(element).backgroundColor);
  await page.getByRole("button", { name: "پوسته روشن", exact: true }).click();
  const light = await page
    .locator("body")
    .evaluate((element) => getComputedStyle(element).backgroundColor);
  expect(light).not.toBe(dark);
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(
    page.getByRole("heading", { name: "مرکز تصمیم", exact: true }),
  ).toBeVisible();
  for (const width of [390, 760, 1024, 1366, 1672]) {
    await page.setViewportSize({ width, height: 941 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await expect(page.locator(".trade-disabled")).toBeDisabled();
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .getByRole("button", { name: "باز کردن فهرست", exact: true })
    .click();
  await page.getByRole("link", { name: "نمای کلی", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "نمای کلی", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "باز کردن فهرست", exact: true })
    .click();
  await page.getByRole("link", { name: "مرکز تصمیم", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "باز کردن فهرست", exact: true }),
  ).toHaveAttribute("aria-expanded", "false");
  await expect(
    page.getByRole("heading", { name: "مرکز تصمیم", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "پوسته تیره", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});


test("historical owner-reported summary stays separate from DEMO and authorization", async ({ page }) => {
  await page.goto("/#decision-center");
  const historical = page.getByRole("region", { name: "ارزیابی تاریخی TP-1 · BTCTMN", exact: true });
  await expect(historical).toContainText("HISTORICAL / OFFLINE");
  await historical.locator("summary").click();
  const record = JSON.parse(await historical.locator("pre").innerText());
  expect(record).toMatchObject({
    source: "OWNER_REPORTED_HISTORICAL_RESULT",
    mode: "HISTORICAL_OFFLINE", symbol: "BTCTMN",
    h1_candles: 264, m15_candles: 24, evaluated_steps: 4,
    eligible_steps: 0, errors: 0, action: "NO_TRADE", executable_ready: false,
    original_artifact_included: false, dataset_sha256: null, range_utc: null, cutoff_utc: null,
    account_balances: "UNVERIFIED", credit_evidence: "UNVERIFIED", fees: "UNVERIFIED",
    quote_freshness: "UNVERIFIED", execution_permissions: "UNVERIFIED",
  });
  await expect(page.getByRole("note")).toContainText("DEMO");
  await expect(page.locator(".trade-disabled")).toBeDisabled();
  await page.getByRole("button", { name: "پوسته روشن", exact: true }).click();
  await expect(historical).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
