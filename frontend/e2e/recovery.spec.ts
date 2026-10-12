import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

function unique(label: string): string {
  return `${label}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

async function registerWithBusiness(page: Page): Promise<void> {
  await page.goto("/register");
  await page.getByLabel("Your name").fill("Recovery Tester");
  await page.getByLabel("Work email").fill(`${unique("recovery")}@example.com`);
  await page.getByLabel("Organization name").fill(unique("Recovery Org"));
  await page.getByLabel("Password", { exact: true }).fill("correct-horse-battery-9");
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page).toHaveURL("/");
  await page.getByRole("link", { name: "Create business" }).click();
  await expect(page).toHaveURL(/\/settings/);
  await page.getByLabel("New business name").fill(unique("Recovery Biz"));
  await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/v1/businesses/") && r.request().method() === "POST"),
    page.getByRole("button", { name: "Create business" }).click(),
  ]);
  await page.goto("/");
}

// These run against a brand-new workspace, so nothing here can send a message.
test.describe("failed message recovery with nothing failed", () => {
  test("is in the Simple navigation and shows an honest empty state", async ({ page }) => {
    await registerWithBusiness(page);
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Failed messages" }).click();
    await expect(page).toHaveURL(/\/failed-messages/);
    await expect(page.getByRole("heading", { name: "Failed messages" })).toBeVisible();
    await expect(page.getByText("No failed messages", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: /retry/i })).toHaveCount(0);
  });

  test("the command box answers from the same endpoint", async ({ page }) => {
    await registerWithBusiness(page);
    await page.getByLabel("Type a command or a question").fill("Which messages failed this week and can I retry them?");
    await page.getByRole("button", { name: /Run/ }).click();
    await expect(page.getByRole("heading", { name: "No failed messages in the last 7 days" })).toBeVisible();
  });

  test("the API refuses a retry of a message that does not exist, and sends nothing", async ({ page }) => {
    await registerWithBusiness(page);
    const status = await page.evaluate(async () => {
      const orgs = (await (await fetch("/api/v1/organizations/")).json()) as { organization: { id: string } }[];
      const headers = { "X-Organization-ID": orgs[0].organization.id, "Content-Type": "application/json" };
      const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
      const id = "00000000-0000-0000-0000-000000000000";
      const response = await fetch(`/api/v1/messages/${id}/retry/`, {
        method: "POST",
        headers: { ...headers, "X-CSRFToken": csrf },
        body: JSON.stringify({ confirm: true }),
      });
      return response.status;
    });
    expect(status).toBe(404);
  });
});
