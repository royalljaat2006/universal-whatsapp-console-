import { expect, test } from "@playwright/test";

function unique(label: string): string {
  return `${label}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

test("a new owner can create a business from the onboarding checklist's link", async ({ page }) => {
  const email = `${unique("e2e-biz")}@example.com`;
  const password = "correct-horse-battery-9";
  const businessName = unique("Kiosk");

  await page.goto("/register");
  await page.getByLabel("Your name").fill("Biz Tester");
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Organization name").fill(unique("Biz Org"));
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page).toHaveURL("/");

  // Zero businesses -> the checklist's first, unchecked step links to Settings.
  await expect(page.getByText("Create a business")).toBeVisible();
  await page.getByRole("link", { name: "Create business" }).click();
  await expect(page).toHaveURL(/\/settings/);

  await page.getByLabel("New business name").fill(businessName);
  await page.getByRole("button", { name: "Create business" }).click();

  await expect(page.getByText(businessName)).toBeVisible();
});
