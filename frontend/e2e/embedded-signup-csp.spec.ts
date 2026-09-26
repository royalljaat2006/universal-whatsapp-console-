import { expect, test } from "@playwright/test";

function unique(label: string): string {
  return `${label}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

/**
 * The riskiest path for the nonce/strict-dynamic CSP (middleware.ts): the
 * Facebook JS SDK script tag that embedded-signup-button.tsx creates at
 * runtime via document.createElement("script"), not a static <script src>
 * Next.js can nonce itself. 'strict-dynamic' should extend trust to it
 * transitively; this proves that, rather than assuming it.
 */
test("the dynamically-injected Facebook SDK script is not blocked by CSP", async ({ page }) => {
  const violations: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error" && /content security policy/i.test(msg.text())) {
      violations.push(msg.text());
    }
  });

  const email = `${unique("e2e-fb")}@example.com`;
  await page.goto("/register");
  await page.getByLabel("Your name").fill("FB CSP Tester");
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Organization name").fill(unique("FB CSP Org"));
  await page.getByLabel("Password", { exact: true }).fill("correct-horse-battery-9");
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page).toHaveURL("/");

  // A business is required for /numbers to render anything at all.
  await page.getByRole("link", { name: "Create business" }).click();
  await expect(page).toHaveURL(/\/settings/);
  await page.getByLabel("New business name").fill(unique("Kiosk"));
  await page.getByRole("button", { name: "Create business" }).click();

  await page.goto("/numbers");
  await page.getByRole("button", { name: "Connect WhatsApp" }).first().click();

  // The button's own click handler awaits Promise.all([config, sdk load]) —
  // give the dynamically-injected <script> a moment to attempt loading.
  await page.waitForTimeout(2000);
  const sdkScriptExists = await page
    .locator('script[src="https://connect.facebook.net/en_US/sdk.js"]')
    .count();
  expect(sdkScriptExists).toBeGreaterThan(0);
  expect(violations, violations.join("\n")).toHaveLength(0);
});
