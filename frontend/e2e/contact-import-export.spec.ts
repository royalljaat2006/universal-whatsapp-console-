import { expect, test } from "@playwright/test";

function unique(label: string): string {
  return `${label}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

test("importing a CSV creates a contact, which is then visible and exportable", async ({ page }) => {
  const email = `${unique("e2e-csv")}@example.com`;
  const phone = `91987${Math.floor(1000000 + Math.random() * 8999999)}`;
  const contactName = unique("Imported Contact");

  await page.goto("/register");
  await page.getByLabel("Your name").fill("CSV Tester");
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Organization name").fill(unique("CSV Org"));
  await page.getByLabel("Password", { exact: true }).fill("correct-horse-battery-9");
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page).toHaveURL("/");

  await page.getByRole("link", { name: "Create business" }).click();
  await expect(page).toHaveURL(/\/settings/);
  await page.getByLabel("New business name").fill(unique("CSV Biz"));
  await page.getByRole("button", { name: "Create business" }).click();

  await page.goto("/contacts");
  await page.getByRole("button", { name: "Import CSV" }).click();
  const csv = `phone_number,display_name\n${phone},${contactName}\n`;
  await page.getByLabel("CSV file").setInputFiles({
    name: "contacts.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(csv),
  });
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByText(/1 created/)).toBeVisible({ timeout: 10000 });

  // The list only reloads on completion via the button's own callback, but
  // reloading the page is the most robust way to confirm it actually
  // persisted server-side, not just an optimistic client-side illusion.
  await page.reload();
  await expect(page.getByText(contactName)).toBeVisible();

  const [download] = await Promise.all([
    page.waitForEvent("download", { timeout: 15000 }),
    page.getByRole("button", { name: "Export CSV" }).click(),
  ]);
  expect(download.suggestedFilename()).toContain(".csv");
});
