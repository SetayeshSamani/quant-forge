import { expect, test } from "@playwright/test";

test("credit evidence states never imply verified equity, capacity or execution", async ({
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
  await page.goto("/?view=demo#credit-risk");
  await expect(
    page.getByRole("heading", { name: "اعتبار و ریسک", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("note")).toContainText("DEMO");
  await expect(page.getByRole("status")).toContainText(
    "ریسک لیکوییدشدن قابل ارزیابی نیست",
  );
  await expect(page.locator(".credit-metrics .metric-card")).toHaveCount(4);
  await expect(
    page.locator('.credit-metric[data-evidence-kind="synthetic"]'),
  ).toHaveCount(4);
  await expect(page.locator(".credit-details .evidence-value")).toHaveCount(5);
  await expect(
    page.locator(
      '.credit-details .evidence-value[data-evidence-kind="synthetic"]',
    ),
  ).toHaveCount(5);
  await expect(page.locator(".credit-details")).toContainText(
    "2026-10-08 14:20 UTC",
  );
  const capacity = page.locator(".capacity-unavailable .evidence-value");
  await expect(capacity).toHaveAttribute("data-evidence-kind", "unavailable");
  await expect(capacity.locator(".evidence-missing")).toHaveText("—");
  await expect(capacity).not.toContainText("۴٬۸۲۰");
  await expect(page.getByRole("progressbar")).toHaveAttribute(
    "aria-label",
    /مصنوعی؛ نه معیار لیکوییدشدن/,
  );
  await expect(page.locator(".credit-limit-note")).toContainText(
    "پروفایل ریسک را فعال نمی‌کنند",
  );
  await expect(page.locator(".local-account-status")).toHaveAttribute(
    "data-evidence-kind",
    "known",
  );
  await expect(page.locator(".local-account-status")).toContainText(
    "هیچ حساب یا API متصل نیست",
  );
  await page.locator(".credit-verification summary").click();
  const verification = page.locator(".credit-verification");
  await expect(
    verification.locator('[data-evidence-kind="unverified"]'),
  ).toHaveCount(1);
  await expect(
    verification.locator('[data-evidence-kind="unavailable"]'),
  ).toHaveCount(2);
  await expect(verification).toContainText("از موجودی کیف پول استنتاج نمی‌شود");
  await expect(verification).toContainText(
    "NO_TRADE · executable_ready = false",
  );
  await expect(page.locator(".risk-equity-unavailable")).toHaveAttribute(
    "data-evidence-kind",
    "unavailable",
  );
  await expect(page.locator(".credit-holdings")).toContainText(
    "ارزش دارایی‌ها، ارزش ویژه مورد تأیید ریسک نیست",
  );
  await expect(page.locator(".account-empty strong")).toHaveText(
    "حساب بررسی نشده است",
  );
  for (const control of await page
    .locator(".credit-risk-page button, .trade-disabled")
    .all())
    await expect(control).toBeDisabled();
  expect(errors).toEqual([]);
  expect(unexpectedRequests).toEqual([]);
});

test("all three pages retain navigation, themes and responsive credit layout", async ({
  page,
}) => {
  await page.goto("/?view=demo");
  for (const [label, hash] of [
    ["اعتبار و ریسک", "credit-risk"],
    ["مرکز تصمیم", "decision-center"],
    ["نمای کلی", "overview"],
    ["اعتبار و ریسک", "credit-risk"],
  ]) {
    const link = page.getByRole("link", { name: label, exact: true });
    await link.click();
    await expect(page).toHaveURL(new RegExp(`#${hash}$`));
    await expect(
      page.getByRole("heading", { name: label, exact: true }),
    ).toBeVisible();
    await expect(link).toHaveAttribute("aria-current", "page");
    await expect(page.locator('.sidebar [aria-current="page"]')).toHaveCount(1);
  }
  await expect(page).toHaveTitle("اعتبار و ریسک | QuantForge DEMO");
  await page.getByRole("button", { name: "پوسته روشن", exact: true }).click();
  const light = await page
    .locator("body")
    .evaluate((element) => getComputedStyle(element).backgroundColor);
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(
    page.getByRole("heading", { name: "اعتبار و ریسک", exact: true }),
  ).toBeVisible();
  for (const width of [390, 760, 1024, 1366, 1672]) {
    await page.setViewportSize({ width, height: 941 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .getByRole("button", { name: "باز کردن فهرست", exact: true })
    .click();
  await page.getByRole("link", { name: "مرکز تصمیم", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "مرکز تصمیم", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "باز کردن فهرست", exact: true })
    .click();
  await page.getByRole("link", { name: "اعتبار و ریسک", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "باز کردن فهرست", exact: true }),
  ).toHaveAttribute("aria-expanded", "false");
  await page.getByRole("button", { name: "پوسته تیره", exact: true }).click();
  expect(
    await page
      .locator("body")
      .evaluate((element) => getComputedStyle(element).backgroundColor),
  ).not.toBe(light);
  await page.goBack();
  await expect(
    page.getByRole("heading", { name: "مرکز تصمیم", exact: true }),
  ).toBeVisible();
});
