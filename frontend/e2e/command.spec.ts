import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

function unique(label: string): string {
  return `${label}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

async function registerWithBusiness(page: Page): Promise<void> {
  await page.goto("/register");
  await page.getByLabel("Your name").fill("Command Tester");
  await page.getByLabel("Work email").fill(`${unique("cmd")}@example.com`);
  await page.getByLabel("Organization name").fill(unique("Cmd Org"));
  await page.getByLabel("Password", { exact: true }).fill("correct-horse-battery-9");
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page).toHaveURL("/");

  await page.getByRole("link", { name: "Create business" }).click();
  await expect(page).toHaveURL(/\/settings/);
  await page.getByLabel("New business name").fill(unique("Cmd Biz"));
  await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/v1/businesses/") && r.request().method() === "POST"),
    page.getByRole("button", { name: "Create business" }).click(),
  ]);
  await page.goto("/");
}

async function ask(page: Page, text: string): Promise<void> {
  await page.getByLabel("Type a command or a question").fill(text);
  await page.getByRole("button", { name: /Run/ }).click();
}

test.describe("command home", () => {
  test("answers from real data and refuses what it cannot do", async ({ page }) => {
    await registerWithBusiness(page);
    await expect(page.getByRole("heading", { name: /what would you like to do/i })).toBeVisible();

    // Real analytics call on a brand-new business: an honest empty state, not zeros.
    await ask(page, "Show today's message delivery performance");
    await expect(page.getByRole("heading", { name: "No message activity today" })).toBeVisible();

    await ask(page, "Find messages that failed this week");
    await expect(page.getByRole("heading", { name: "No failed messages in the last 7 days" })).toBeVisible();

    await ask(page, "Show campaigns currently running");
    await expect(page.getByRole("heading", { name: "No campaigns are running" })).toBeVisible();

    await ask(page, "Show unread conversations");
    await expect(page.getByRole("heading", { name: "Nothing unread" })).toBeVisible();

    // Unsupported requests are refused, never guessed at.
    await ask(page, "delete all my contacts");
    await expect(page.getByRole("heading", { name: "I can't do that from here" })).toBeVisible();
    await expect(page.getByText(/Nothing was changed/)).toBeVisible();

    // Launching something that doesn't exist launches nothing.
    await ask(page, "launch campaign Does Not Exist");
    await expect(page.getByRole("heading", { name: /No campaign matches/ })).toBeVisible();

    // Navigation is a plain route change.
    await ask(page, "open templates");
    await expect(page).toHaveURL(/\/templates/);
  });

  test("the full console stays one click away behind the overview disclosure", async ({ page }) => {
    await registerWithBusiness(page);
    await page.getByRole("button", { name: "Business overview" }).click();
    await expect(page.getByText("Messages today")).toBeVisible();
    for (const link of ["Inbox", "Contacts", "Templates", "Campaigns", "Analytics"]) {
      await expect(page.getByRole("link", { name: link, exact: true }).first()).toBeVisible();
    }
  });
});
