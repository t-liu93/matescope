import { expect, test } from "@playwright/test";

test("serves the compiled bilingual application shell", async ({ page }) => {
  await page.goto("/");

  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByRole("heading", { name: "MateScope" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "The application shell is ready." })).toBeVisible();
  await expect(page.getByText("Connected")).toBeVisible();

  await page.getByRole("button", { name: "中文" }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh");
  await expect(page.getByRole("heading", { name: "应用外壳已就绪。" })).toBeVisible();
  await expect(page.getByRole("button", { name: "English" })).toBeVisible();
});
