import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

function unique(label: string): string {
  return `${label}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

async function registerWithBusiness(page: Page): Promise<void> {
  await page.goto("/register");
  await page.getByLabel("Your name").fill("Mode Tester");
  await page.getByLabel("Work email").fill(`${unique("mode")}@example.com`);
  await page.getByLabel("Organization name").fill(unique("Mode Org"));
  await page.getByLabel("Password", { exact: true }).fill("correct-horse-battery-9");
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page).toHaveURL("/");
  await page.getByRole("link", { name: "Create business" }).click();
  await expect(page).toHaveURL(/\/settings/);
  await page.getByLabel("New business name").fill(unique("Mode Biz"));
  await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/v1/businesses/") && r.request().method() === "POST"),
    page.getByRole("button", { name: "Create business" }).click(),
  ]);
  await page.goto("/");
}

async function chooseMode(page: Page, mode: "Simple" | "Advanced"): Promise<void> {
  await page.getByRole("button", { name: /Account menu/ }).click();
  await page.getByRole("menuitemradio", { name: new RegExp(`^${mode}`) }).click();
}

const sidebar = (page: Page) => page.getByRole("navigation", { name: "Main" });

test.describe("simple and advanced mode", () => {
  test("starts simple, switches to advanced, remembers the choice, and can go back", async ({ page }) => {
    await registerWithBusiness(page);

    // Simple: the everyday essentials only, in plain words.
    for (const name of [
      "Home",
      "Inbox",
      "Contacts",
      "Templates",
      "Campaigns",
      "WhatsApp numbers",
      "Settings",
    ]) {
      await expect(sidebar(page).getByRole("link", { name, exact: true })).toBeVisible();
    }
    await expect(sidebar(page).getByRole("link", { name: "Analytics" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Business overview" })).toHaveCount(0);

    // Advanced: everything the person already had.
    await chooseMode(page, "Advanced");
    await expect(sidebar(page).getByRole("link", { name: "Analytics", exact: true })).toBeVisible();
    await expect(sidebar(page).getByRole("link", { name: "Overview", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Business overview" }).click();
    await expect(page.getByText("Messages today")).toBeVisible();

    // The choice survives a reload (stored in this browser).
    await page.reload();
    await expect(sidebar(page).getByRole("link", { name: "Analytics", exact: true })).toBeVisible();

    // And back to simple.
    await chooseMode(page, "Simple");
    await expect(sidebar(page).getByRole("link", { name: "Analytics" })).toHaveCount(0);
  });

  test("technical settings are advanced-only, with a plain pointer in simple mode", async ({ page }) => {
    await registerWithBusiness(page);
    await page.goto("/settings");

    const settingsNav = page.getByRole("navigation", { name: "Settings sections" });
    await expect(settingsNav.getByRole("button", { name: "Your account" })).toBeVisible();
    await expect(settingsNav.getByRole("button", { name: "Roles" })).toHaveCount(0);
    await expect(settingsNav.getByRole("button", { name: "API keys" })).toHaveCount(0);
    await expect(settingsNav.getByRole("button", { name: "Audit log" })).toHaveCount(0);
    await expect(page.getByText(/more technical settings?/i)).toBeVisible();

    await page.getByRole("button", { name: "Switch to Advanced" }).click();
    await expect(settingsNav.getByRole("button", { name: "Roles" })).toBeVisible();
    await expect(settingsNav.getByRole("button", { name: "API keys" })).toBeVisible();
    await expect(settingsNav.getByRole("button", { name: "Audit log" })).toBeVisible();
  });

  test("mode never changes what the server allows", async ({ page }) => {
    await registerWithBusiness(page);
    // The same API answers identically whichever mode the UI is in.
    const status = () => page.evaluate(async () => (await fetch("/api/v1/organizations/")).status);
    const simple = await status();
    await chooseMode(page, "Advanced");
    expect(await status()).toBe(simple);
  });
});
