import { expect, test } from "@playwright/test";

function unique(label: string): string {
  return `${label}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

/**
 * middleware.ts sets a nonce + strict-dynamic CSP for every page. This is
 * exactly the kind of thing that silently regresses the moment someone adds
 * a new external script/style/image host without updating the policy — so
 * this walks the authenticated app surface and fails on any real CSP
 * violation report, not just "the page rendered something".
 */
test("no Content-Security-Policy violations across the authenticated app", async ({ page }) => {
  const violations: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error" && /content security policy/i.test(msg.text())) {
      violations.push(msg.text());
    }
  });

  const email = `${unique("e2e-csp")}@example.com`;
  await page.goto("/register");
  await page.getByLabel("Your name").fill("CSP Tester");
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Organization name").fill(unique("CSP Org"));
  await page.getByLabel("Password", { exact: true }).fill("correct-horse-battery-9");
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page).toHaveURL("/");

  for (const path of ["/contacts", "/campaigns", "/numbers", "/analytics", "/templates", "/settings", "/inbox"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
  }

  expect(violations, violations.join("\n")).toHaveLength(0);
});
