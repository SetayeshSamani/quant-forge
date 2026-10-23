import { expect, test } from "@playwright/test";

test("overview is demo-only and theme selection persists", async ({ page }) => {
  const errors: string[] = [];
  const externalRequests: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (
      !request.url().startsWith("http://127.0.0.1:5173") &&
      !request.url().startsWith("data:")
    )
      externalRequests.push(request.url());
  });
  await page.goto("/?view=demo");
  await expect(
    page.getByRole("heading", { name: "نمای کلی", exact: true }),
  ).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "fa");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.getByRole("button", { name: /NO_TRADE/ })).toBeDisabled();
  for (const metric of await page.locator(".metric-card").all()) {
    const icon = await metric.locator(".metric-icon").boundingBox();
    const value = await metric.locator(".metric-copy strong").boundingBox();
    expect(icon && value && icon.x + icon.width <= value.x).toBe(true);
  }
  await expect(page.getByRole("note")).toContainText("داده‌های نمایشی");
  await expect(
    page.getByRole("link", { name: "مرکز تصمیم", exact: true }),
  ).toHaveAttribute("href", "#decision-center");
  await page.getByRole("button", { name: "پوسته روشن" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.reload();
  await expect(
    page.getByRole("button", { name: "پوسته روشن" }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "پوسته تیره" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("button", { name: "1H", exact: true }).click();
  await expect(page.getByRole("img", { name: /بازه 1H/ })).toBeVisible();
  await page.getByRole("button", { name: "مشاهده همه" }).click();
  await expect(page.locator("tbody tr")).toHaveCount(5);
  await page.getByRole("button", { name: "بررسی ریسک" }).click();
  await expect(page.getByRole("dialog")).toContainText(
    "executable_ready = false",
  );
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  expect(errors).toEqual([]);
  expect(externalRequests).toEqual([]);
});

test("mobile navigation works without page overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?view=demo");
  await page.getByRole("button", { name: "باز کردن فهرست" }).click();
  await expect(
    page.getByRole("button", { name: "باز کردن فهرست" }),
  ).toHaveAttribute("aria-expanded", "true");
  await page.getByRole("link", { name: "نمای کلی", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "باز کردن فهرست" }),
  ).toHaveAttribute("aria-expanded", "false");
  for (const width of [390, 760, 1024, 1366, 1672]) {
    await page.setViewportSize({ width, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "هشدارهای نمایشی" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "متوجه شدم" }).click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
});
