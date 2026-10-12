import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

function unique(label: string): string {
  return `${label}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

async function registerWithBusiness(page: Page): Promise<void> {
  await page.goto("/register");
  await page.getByLabel("Your name").fill("Template Tester");
  await page.getByLabel("Work email").fill(`${unique("tpl")}@example.com`);
  await page.getByLabel("Organization name").fill(unique("Tpl Org"));
  await page.getByLabel("Password", { exact: true }).fill("correct-horse-battery-9");
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page).toHaveURL("/");
  await page.getByRole("link", { name: "Create business" }).click();
  await expect(page).toHaveURL(/\/settings/);
  await page.getByLabel("New business name").fill(unique("Tpl Biz"));
  await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/v1/businesses/") && r.request().method() === "POST"),
    page.getByRole("button", { name: "Create business" }).click(),
  ]);
  await page.goto("/");
}

async function api(page: Page, method: string, path: string, body?: unknown) {
  return page.evaluate(
    async ({ method, path, body }) => {
      const orgs = (await (await fetch("/api/v1/organizations/")).json()) as { organization: { id: string } }[];
      const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
      const r = await fetch(path, {
        method,
        headers: { "X-Organization-ID": orgs[0].organization.id, "Content-Type": "application/json", "X-CSRFToken": csrf },
        body: body ? JSON.stringify(body) : undefined,
      });
      const text = await r.text();
      return { status: r.status, json: text ? JSON.parse(text) : null };
    },
    { method, path, body },
  );
}

// A brand-new workspace has no WhatsApp account, so nothing here can reach Meta.
test.describe("template studio with no WhatsApp account", () => {
  test("the page offers no way to write a template and says why", async ({ page }) => {
    await registerWithBusiness(page);
    await page.goto("/templates");
    await expect(page.getByText("No WhatsApp Business Account connected")).toBeVisible();
    await expect(page.getByRole("button", { name: "New template" })).toBeDisabled();
    // Opening via the link must not crash or open a broken studio.
    await page.goto("/templates?new=1");
    await expect(page.getByText("No WhatsApp Business Account connected")).toBeVisible();
  });

  test("the API states Meta's rules and what is unsupported", async ({ page }) => {
    await registerWithBusiness(page);
    const options = await api(page, "GET", "/api/v1/templates/studio/options/");
    expect(options.status).toBe(200);
    expect(options.json.limits.body).toBe(1024);
    expect(options.json.categories.find((c: { value: string }) => c.value === "AUTHENTICATION").supported).toBe(false);
    expect(options.json.unsupported.length).toBeGreaterThan(0);
    expect(options.json.starters.length).toBeGreaterThan(0);
  });

  test("drafts, validation and submit refuse accounts and drafts that are not yours, and send nothing", async ({ page }) => {
    await registerWithBusiness(page);
    const nobody = "00000000-0000-0000-0000-000000000000";
    const body = {
      business_account_id: nobody,
      name: "order_shipped",
      language: "en_US",
      category: "UTILITY",
      parameter_format: "POSITIONAL",
      content: { header_text: "", body: "Hi {{1}}, your order is on its way", examples: { "1": "Asha" }, footer: "", buttons: [] },
    };
    expect((await api(page, "POST", "/api/v1/templates/drafts/", body)).status).toBe(404);
    expect((await api(page, "POST", "/api/v1/templates/studio/validate/", body)).status).toBe(404);
    expect((await api(page, "POST", `/api/v1/templates/drafts/${nobody}/submit/`, { confirm: true, content_hash: "x" })).status).toBe(404);
    expect((await api(page, "GET", "/api/v1/templates/drafts/")).json.results).toEqual([]);
  });

  test("the command box points to the studio and never creates or sends anything", async ({ page }) => {
    await registerWithBusiness(page);
    await page.getByLabel("Type a command or a question").fill("I want to create a new template");
    await page.getByRole("button", { name: /Run/ }).click();
    await expect(page.getByRole("heading", { name: "Write a template in the Template Studio" })).toBeVisible();
    await expect(page.getByText(/not approved until Meta says so/)).toBeVisible();
    await expect(page.getByRole("link", { name: "Open the Template Studio" })).toHaveAttribute("href", "/templates?new=1");
  });
});
