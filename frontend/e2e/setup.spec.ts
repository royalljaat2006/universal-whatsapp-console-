import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

function unique(label: string): string {
  return `${label}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

async function registerWithBusiness(page: Page): Promise<void> {
  await page.goto("/register");
  await page.getByLabel("Your name").fill("Setup Tester");
  await page.getByLabel("Work email").fill(`${unique("setup")}@example.com`);
  await page.getByLabel("Organization name").fill(unique("Setup Org"));
  await page.getByLabel("Password", { exact: true }).fill("correct-horse-battery-9");
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page).toHaveURL("/");
  await page.getByRole("link", { name: "Create business" }).click();
  await expect(page).toHaveURL(/\/settings/);
  await page.getByLabel("New business name").fill(unique("Setup Biz"));
  await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/v1/businesses/") && r.request().method() === "POST"),
    page.getByRole("button", { name: "Create business" }).click(),
  ]);
  await page.goto("/");
}

// A brand-new workspace has no WhatsApp account, so nothing here can send a message.
test.describe("whatsapp setup with nothing connected", () => {
  test("is in the Simple navigation and shows honest, unverified steps", async ({ page }) => {
    await registerWithBusiness(page);
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Set up WhatsApp" }).click();
    await expect(page).toHaveURL(/\/setup/);
    await expect(page.getByRole("heading", { name: "Set up WhatsApp" })).toBeVisible();
    await expect(page.getByText("WhatsApp is not connected yet.")).toBeVisible();
    // No step claims to be done, and nothing says "Ready".
    await expect(page.getByText("Done", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Ready", { exact: true })).toHaveCount(0);
    // Test message cannot be started without a connected number.
    await expect(page.getByText(/Connect a number first/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Send test message" })).toHaveCount(0);
  });

  test("the API reports not_started and no step is verified", async ({ page }) => {
    await registerWithBusiness(page);
    const body = await page.evaluate(async () => {
      const orgs = (await (await fetch("/api/v1/organizations/")).json()) as { organization: { id: string } }[];
      const r = await fetch("/api/v1/whatsapp/setup/", { headers: { "X-Organization-ID": orgs[0].organization.id } });
      return { status: r.status, json: await r.json() };
    });
    expect(body.status).toBe(200);
    expect(body.json.overall).toBe("not_started");
    expect(body.json.steps.every((s: { state: string }) => s.state !== "verified")).toBe(true);
    // Only yes/no flags about server settings, never their values or any token.
    const raw = JSON.stringify(body.json);
    expect(raw).not.toMatch(/access_token|encrypted|password/i);
    expect(raw).not.toMatch(/[A-Za-z0-9_-]{40,}/);
    expect(body.json.webhook.verify_token_configured).toBe(true);
  });

  test("the real send endpoint refuses a forged approval and sends nothing", async ({ page }) => {
    await registerWithBusiness(page);
    const status = await page.evaluate(async () => {
      const orgs = (await (await fetch("/api/v1/organizations/")).json()) as { organization: { id: string } }[];
      const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
      const r = await fetch("/api/v1/whatsapp/test-message/send/", {
        method: "POST",
        headers: {
          "X-Organization-ID": orgs[0].organization.id,
          "Content-Type": "application/json",
          "X-CSRFToken": csrf,
        },
        body: JSON.stringify({ approval_token: "forged-token", approve: true }),
      });
      return r.status;
    });
    expect(status).toBe(409);
  });

  test("the command box points to setup and never sends from the box", async ({ page }) => {
    await registerWithBusiness(page);
    await page.getByLabel("Type a command or a question").fill("I want to send a test message");
    await page.getByRole("button", { name: /Run/ }).click();
    await expect(page.getByRole("heading", { name: "Send a test message from the Set up page" })).toBeVisible();
    await page.getByLabel("Type a command or a question").fill("Is my WhatsApp set up and ready?");
    await page.getByRole("button", { name: /Run/ }).click();
    await expect(page.getByRole("heading", { name: "WhatsApp isn't set up yet" })).toBeVisible();
  });
});
