import { expect, test } from "@playwright/test";

function unique(label: string): string {
  return `${label}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

test.describe("registration and login", () => {
  test("a new user can register, land on the console, sign out, and log back in", async ({ page }) => {
    const email = `${unique("e2e")}@example.com`;
    const password = "correct-horse-battery-9";

    await page.goto("/register");
    await page.getByLabel("Your name").fill("E2E Tester");
    await page.getByLabel("Work email").fill(email);
    await page.getByLabel("Organization name").fill(unique("E2E Org"));
    await page.getByLabel("Password", { exact: true }).fill(password);
    await page.getByRole("button", { name: "Create workspace" }).click();

    // Registration logs the user in and redirects to the console.
    await expect(page).toHaveURL("/");
    await expect(page.getByRole("heading", { name: /what would you like to do/i })).toBeVisible();
    // A brand-new organization has no business yet — the onboarding
    // checklist (not an indefinite loading skeleton) should be visible.
    await expect(page.getByText("Finish setting up your workspace")).toBeVisible();

    await page.getByRole("button", { name: /Account menu/ }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login$/);

    await page.getByLabel("Work email").fill(email);
    await page.getByLabel("Password", { exact: true }).fill(password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL("/");
    await expect(page.getByRole("heading", { name: /what would you like to do/i })).toBeVisible();
  });

  test("an unregistered email is rejected on login with a visible error", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Work email").fill(`${unique("nobody")}@example.com`);
    await page.getByLabel("Password", { exact: true }).fill("whatever-wrong-password");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page).toHaveURL(/\/login/);
  });
});
