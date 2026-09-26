import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

function unique(label: string): string {
  return `${label}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

async function registerAndCreateBusiness(page: Page, label: string): Promise<string> {
  const email = `${unique(label)}@example.com`;
  await page.goto("/register");
  await page.getByLabel("Your name").fill("Isolation Tester");
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Organization name").fill(unique(label));
  await page.getByLabel("Password", { exact: true }).fill("correct-horse-battery-9");
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page).toHaveURL("/");

  await page.getByRole("link", { name: "Create business" }).click();
  await expect(page).toHaveURL(/\/settings/);
  await page.getByLabel("New business name").fill(unique(`${label}-biz`));
  const [response] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes("/api/v1/businesses/") && r.request().method() === "POST",
    ),
    page.getByRole("button", { name: "Create business" }).click(),
  ]);
  const body = await response.json();
  return body.id as string;
}

test("a business created in one organization is invisible to another — even by direct id, through the real API path", async ({
  browser,
}) => {
  const contextA = await browser.newContext();
  const businessIdA = await registerAndCreateBusiness(await contextA.newPage(), "iso-a");

  const contextB = await browser.newContext();
  const pageB = await contextB.newPage();
  await registerAndCreateBusiness(pageB, "iso-b");

  // From inside org B's own authenticated page context (real cookies, real
  // origin) — ask the API for B's own organization id (the same call the
  // app itself makes on load), then use it to probe A's business, rather
  // than a synthetic out-of-band request with a guessed org id.
  const status = await pageB.evaluate(async (id) => {
    const memberships = await (await fetch("/api/v1/organizations/")).json();
    const orgId = memberships[0]?.organization?.id ?? "";
    const res = await fetch(`/api/v1/businesses/${id}/`, {
      headers: { "X-Organization-ID": orgId },
    });
    return res.status;
  }, businessIdA);

  expect(status).toBe(404);

  await contextA.close();
  await contextB.close();
});
