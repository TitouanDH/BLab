// Smoke flows on the mocked API (mock-api.js): log in, and the feedback
// rule (results as toasts, server messages in plain sentences, "Cannot reach BLab").
import { test, expect } from './fixtures.js';

const card = (page, ip) => page.locator(`[data-switch="${ip}"]`);
const toast = (page) => page.locator('[data-kind]');

test.describe('logged out', () => {
  test.use({ loggedInAs: null });

  test('log in', async ({ page, api }) => {
    await page.goto('/login');
    await page.getByLabel('Username').fill('alice');
    await page.getByLabel('Password').fill('secret');
    await page.getByRole('button', { name: 'Log in' }).click();
    await expect(page).toHaveURL('/');
    await expect(page.getByRole('heading', { name: 'My lab' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Log out' })).toBeVisible();
    expect(api.calls).toContain('POST login/');
  });

  test('a failed log in says why in a toast that stays', async ({ page, api }) => {
    await page.goto('/login');
    await page.getByLabel('Username').fill('alice');
    await page.getByLabel('Password').fill('wrong');
    await page.getByRole('button', { name: 'Log in' }).click();
    await expect(page.getByRole('alert')).toHaveText('Invalid credentials.');
    await expect(page).toHaveURL('/login');
  });

  test('pages that need a session send to the log in page', async ({ page, api }) => {
    await page.goto('/reservation');
    await expect(page).toHaveURL('/login');
  });
});

test('a refused Reservation shows the server reason, not a generic failure', async ({ page, api }) => {
  const reason = "You can't make new Reservations while the Quarantine of 10.69.145.14 names you (Unwanted cable on 1/1/5). Fix it, then press Re-check on the Lab status page.";
  api.override('POST', 'reserve/', () => ({ status: 403, body: { detail: reason } }));
  await page.goto('/reservation');
  await card(page, '10.69.145.11').getByRole('button', { name: 'Reserve' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Reserve' }).click();

  const alert = page.getByRole('alert');
  await expect(alert).toHaveText(`Couldn't reserve this Switch. ${reason}`);
  // Errors stay until closed
  await page.waitForTimeout(5500);
  await expect(alert).toBeVisible();
  await alert.getByRole('button', { name: 'Close' }).click();
  await expect(alert).toBeHidden();
});

test('success toasts go away by themselves', async ({ page, api }) => {
  await page.goto('/reservation');
  await card(page, '10.69.145.11').getByRole('button', { name: 'Reserve' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Reserve' }).click();
  const success = page.getByRole('status').filter({ hasText: 'Reservation successful.' });
  await expect(success).toBeVisible();
  await expect(success).toBeHidden({ timeout: 8000 });
});

test('raw backbone output never reaches the user', async ({ page, api }) => {
  api.override('POST', 'connect/', () => ({ status: 422, body: {
    detail: "Ports failed to connect: 'ethernet-service svlan 1002 name blab_1002' failed on 10.69.144.1 (diag 2): ERROR: Invalid entity" } }));
  await page.goto('/status');
  const result = await page.evaluate(async () => {
    const { portService } = await import('/src/utils/apiService.js');
    return portService.connect(11, 22);
  });
  expect(result.success).toBe(false);
  expect(result.message).not.toContain('diag');
  expect(result.message).not.toContain('ethernet-service');
  expect(result.message).toMatch(/^The Link couldn't be made/);
});

test('a failing poll shows a discreet "Cannot reach BLab", gone once BLab answers', async ({ page, api }) => {
  await page.goto('/reservation');
  await expect(card(page, '10.69.145.11')).toBeVisible();
  api.goOffline();
  const offline = page.locator('[data-kind="quiet"]');
  await expect(offline).toHaveText(/Cannot reach BLab/, { timeout: 6000 });
  await expect(toast(page)).toHaveCount(1);  // one toast, not one per poll
  await page.waitForTimeout(2500);
  await expect(toast(page)).toHaveCount(1);
  api.goOnline();
  await expect(offline).toBeHidden({ timeout: 6000 });
});

test('log out needs no confirmation', async ({ page, api }) => {
  await page.goto('/status');
  await page.getByRole('button', { name: 'Log out' }).click();
  await expect(page).toHaveURL('/login');
  await expect(page.getByRole('link', { name: /Log in/ })).toBeVisible();
});

test('the phone menu shows readable links', async ({ page, api }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/status');
  await page.getByRole('button', { name: 'Open main menu' }).click();
  const link = page.getByRole('dialog').getByRole('link', { name: 'Reservation' });
  await expect(link).toBeVisible();
  // Dark text on the white panel (it was white on white)
  const color = await link.evaluate(el => getComputedStyle(el).color);
  expect(color).not.toBe('rgb(255, 255, 255)');
  await link.click();
  await expect(page).toHaveURL('/reservation');
  await expect(page.getByRole('dialog')).toBeHidden();
});

test('Lab status: Re-check reports what it found in a toast', async ({ page, api }) => {
  await page.goto('/status');
  await page.getByRole('button', { name: 'Re-check' }).click();
  await expect(page.getByRole('alert')).toHaveText('Still not clean: Unwanted cable on 1/1/5.');
});

test('a Reservation gives me a Switch account, its password shown only on demand', async ({ page, api }) => {
  await page.goto('/reservation');
  const logins = page.getByRole('region', { name: 'Your Switch accounts' });
  // alice holds 10.69.145.12, and bob shares his Topology (10.69.145.13) with her
  await expect(logins.locator('[data-switch-login]')).toHaveCount(2);

  await card(page, '10.69.145.11').getByRole('button', { name: 'Reserve' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Reserve' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Reservation successful.' })).toBeVisible();

  const row = logins.locator('[data-switch-login="10.69.145.11"]');
  await expect(row).toContainText('alice');
  await expect(row.locator('[data-password]')).not.toContainText('Pw-alice-1x');
  await row.getByRole('button', { name: 'Show' }).click();
  await expect(row.locator('[data-password]')).toHaveText('Pw-alice-1x');
});

test('a Switch account BLab could not create yet says so, and the Reservation still stands', async ({ page, api }) => {
  api.state.accountErrors[1] = "Cannot connect to 10.69.145.11: timed out";
  await page.goto('/reservation');
  await card(page, '10.69.145.11').getByRole('button', { name: 'Reserve' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Reserve' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Reservation successful.' })).toBeVisible();
  await expect(page.getByRole('alert')).toContainText("BLab couldn't create your Switch account on 10.69.145.11 yet");
  await expect(page.locator('[data-switch-login="10.69.145.11"]')).toContainText('BLab keeps trying');
});

test('the Topology page leads to my Switch accounts', async ({ page, api }) => {
  await page.goto('/topology');
  await page.getByRole('link', { name: 'My Switch accounts' }).click();
  await expect(page).toHaveURL('/reservation#switch-accounts');
  await expect(page.locator('[data-switch-login="10.69.145.12"]')).toContainText('alice');
});
