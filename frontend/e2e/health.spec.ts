import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

function unique(label: string): string {
  return `${label}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

async function registerWithBusiness(page: Page): Promise<void> {
  await page.goto("/register");
  await page.getByLabel("Your name").fill("Health Tester");
  await page.getByLabel("Work email").fill(`${unique("health")}@example.com`);
  await page.getByLabel("Organization name").fill(unique("Health Org"));
  await page.getByLabel("Password", { exact: true }).fill("correct-horse-battery-9");
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page).toHaveURL("/");
  await page.getByRole("link", { name: "Create business" }).click();
  await expect(page).toHaveURL(/\/settings/);
  await page.getByLabel("New business name").fill(unique("Health Biz"));
  await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/v1/businesses/") && r.request().method() === "POST"),
    page.getByRole("button", { name: "Create business" }).click(),
  ]);
  await page.goto("/");
}

test.describe("whatsapp health with nothing connected", () => {
  test("the API reports unknown, never an invented all-clear", async ({ page }) => {
    await registerWithBusiness(page);
    const body = await page.evaluate(async () => {
      const headers: Record<string, string> = {};
      // Same headers the app sends; read from the stored session via the API.
      const orgs = (await (await fetch("/api/v1/organizations/")).json()) as {
        organization: { id: string };
      }[];
      headers["X-Organization-ID"] = orgs[0].organization.id;
      const response = await fetch("/api/v1/whatsapp/health/", { headers });
      return { status: response.status, json: await response.json() };
    });
    expect(body.status).toBe(200);
    expect(body.json.overall).toBe("unknown");
    expect(body.json.summary).toBe("No WhatsApp account or number is connected yet.");
    expect(body.json.numbers).toEqual([]);
    expect(JSON.stringify(body.json)).not.toMatch(/token|secret/i);
  });

  test("the health command answers honestly and the Home card stays out of the way", async ({ page }) => {
    await registerWithBusiness(page);
    await expect(page.getByRole("region", { name: "WhatsApp health" })).toHaveCount(0);

    await page
      .getByLabel("Type a command or a question")
      .fill("Is everything healthy with my WhatsApp numbers?");
    await page.getByRole("button", { name: /Run/ }).click();
    await expect(page.getByRole("heading", { name: "No WhatsApp number is connected yet" })).toBeVisible();
    await expect(page.getByText("Healthy", { exact: true })).toHaveCount(0);
  });

  test("the numbers page offers setup, not a fake health report", async ({ page }) => {
    await registerWithBusiness(page);
    await page.goto("/numbers");
    await expect(page.getByText(/No WhatsApp Business Account connected/)).toBeVisible();
    await expect(page.getByRole("heading", { name: "Health" })).toHaveCount(0);
  });
});
