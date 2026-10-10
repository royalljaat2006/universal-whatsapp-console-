import { createHmac } from "node:crypto";

import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

function unique(label: string): string {
  return `${label}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

const PASSWORD = "correct-horse-battery-9";

/** RFC 6238 (SHA-1, 6 digits, 30 s), computed independently of the app. */
function totp(base32Secret: string, stepOffset = 0): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const char of base32Secret.replace(/=+$/, "").toUpperCase()) {
    bits += alphabet.indexOf(char).toString(2).padStart(5, "0");
  }
  const bytes = Buffer.from(
    (bits.match(/.{8}/g) ?? []).map((b) => parseInt(b, 2)),
  );
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 1000 / 30) + stepOffset));
  const digest = createHmac("sha1", bytes).update(counter).digest();
  const offset = digest[digest.length - 1] & 0x0f;
  const value = (digest.readUInt32BE(offset) & 0x7fffffff) % 1_000_000;
  return value.toString().padStart(6, "0");
}

async function register(page: Page): Promise<string> {
  const email = `${unique("mfa")}@example.com`;
  await page.goto("/register");
  await page.getByLabel("Your name").fill("MFA Tester");
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Organization name").fill(unique("MFA Org"));
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page).toHaveURL("/");
  return email;
}

// Next.js also renders a role="alert" route announcer; match the app's own error.
function alertText(page: Page) {
  return page.getByRole("alert").filter({ hasText: /verification code is incorrect/i });
}

async function signOut(page: Page): Promise<void> {
  await page.getByRole("button", { name: /Account menu/ }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
}

test.describe("two-step verification", () => {
  test("enrol, then sign-in needs the code; recovery code works once", async ({ page, request }) => {
    const email = await register(page);

    // --- enrol from Settings ---------------------------------------------
    await page.goto("/settings");
    await page.getByRole("button", { name: "Your account" }).click();
    await page.getByLabel("Current password").fill(PASSWORD);
    await page.getByRole("button", { name: "Set up two-step verification" }).click();

    const secret = (await page.locator("code").first().innerText()).trim();
    expect(secret).toMatch(/^[A-Z2-7]{32}$/);

    // A wrong code is refused and nothing is turned on.
    await page.getByLabel("Authentication code").fill("000000");
    await page.getByRole("button", { name: "Turn on two-step verification" }).click();
    await expect(alertText(page)).toContainText(/incorrect|already been used/i);

    await page.getByLabel("Authentication code").fill(totp(secret));
    await page.getByRole("button", { name: "Turn on two-step verification" }).click();

    const codesRegion = page.getByRole("region", { name: "Recovery codes" });
    await expect(codesRegion).toBeVisible();
    const recoveryCodes = (await codesRegion.locator("li").allInnerTexts()).map((c) => c.trim());
    expect(recoveryCodes).toHaveLength(10);
    await page.getByRole("button", { name: "I've saved them" }).click();
    await expect(page.getByText("9 unused recovery codes remaining").or(page.getByText("10 unused recovery codes remaining"))).toBeVisible();

    // --- sign out; password alone must not be enough ------------------------
    await signOut(page);
    await page.getByLabel("Work email").fill(email);
    await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("heading", { name: "Two-step verification" })).toBeVisible();
    await expect(page).toHaveURL(/\/login/);

    // The API agrees: the half-finished sign-in is not a session.
    const me = await page.evaluate(async () => (await fetch("/api/v1/auth/me/")).status);
    expect([401, 403]).toContain(me);

    // Wrong code.
    await page.getByLabel("Authentication code").fill("111111");
    await page.getByRole("button", { name: "Verify and sign in" }).click();
    await expect(alertText(page)).toContainText(/incorrect|already been used/i);

    // Correct code (next time-step: the enrolment code's step is already spent).
    await page.getByLabel("Authentication code").fill(totp(secret, 1));
    await page.getByRole("button", { name: "Verify and sign in" }).click();
    await expect(page).toHaveURL("/");

    // --- recovery code: works once ----------------------------------------
    await signOut(page);
    await page.getByLabel("Work email").fill(email);
    await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.getByRole("button", { name: "Use a recovery code" }).click();
    await page.getByLabel("Recovery code").fill(recoveryCodes[0]);
    await page.getByRole("button", { name: "Verify and sign in" }).click();
    await expect(page).toHaveURL("/");

    await signOut(page);
    await page.getByLabel("Work email").fill(email);
    await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.getByRole("button", { name: "Use a recovery code" }).click();
    await page.getByLabel("Recovery code").fill(recoveryCodes[0]);
    await page.getByRole("button", { name: "Verify and sign in" }).click();
    await expect(alertText(page)).toBeVisible();
    await expect(page).toHaveURL(/\/login/);

    // Unauthenticated API access stays closed.
    expect((await request.get("/api/v1/auth/me/")).status()).toBeGreaterThanOrEqual(401);
  });
});
