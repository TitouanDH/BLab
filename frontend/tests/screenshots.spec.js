// Screenshots of every page against the mocked API, for the before/after pictures an
// issue's closing comment carries. Not part of `npm test`: run `npm run screenshots`,
// with SHOT_PREFIX naming the set (e.g. "before" or "after"). Files go to screenshots/.
import { test } from './fixtures.js';

const prefix = process.env.SHOT_PREFIX || 'shot';
const shot = (page, name) => page.screenshot({ path: `screenshots/${prefix}-${name}.png`, fullPage: true });
const settle = (page) => page.waitForTimeout(800);  // polls answered, transitions done

test.describe('logged out @screenshot', () => {
  test.use({ loggedInAs: null });
  for (const [name, path] of [['home', '/'], ['login', '/login'], ['signup', '/signup']]) {
    test(name, async ({ page, api }) => {
      await page.goto(path);
      await settle(page);
      await shot(page, name);
    });
  }
});

test.describe('logged in @screenshot', () => {
  test('reservation', async ({ page, api }) => {
    await page.goto('/reservation');
    await settle(page);
    await shot(page, 'reservation');
    await page.getByRole('button', { name: /^Free/ }).click();
    await settle(page);
    await shot(page, 'reservation-free');
    await page.getByRole('button', { name: /^All/ }).click();
    await page.getByRole('button', { name: /^Reserve$/ }).first().click();
    await settle(page);
    await shot(page, 'reservation-reserve-dialog');
  });

  test('release dialog', async ({ page, api }) => {
    await page.goto('/reservation');
    await page.getByRole('button', { name: /^Release$/ }).first().click();
    await settle(page);
    await shot(page, 'reservation-release-dialog');
    // The card while its Cleanup runs
    await page.getByRole('dialog').getByRole('button', { name: /^Release$/ }).click();
    await settle(page);
    await shot(page, 'reservation-cleaning-up');
  });

  test('reserve error', async ({ page, api }) => {
    api.override('POST', 'reserve/', () => ({ status: 403, body: { detail: "You can't make new Reservations while the Quarantine of 10.69.145.14 names you (Unwanted cable on 1/1/5). Fix it, then press Re-check on the Lab status page." } }));
    await page.goto('/reservation');
    await page.getByRole('button', { name: /^Reserve$/ }).first().click();
    await page.getByRole('button', { name: /^Reserve( Switch)?$/ }).last().click();
    await settle(page);
    await shot(page, 'reservation-reserve-error');
  });

  test('topology', async ({ page, api }) => {
    await page.goto('/topology');
    await page.waitForTimeout(2500);
    await shot(page, 'topology');
    await page.getByRole('button', { name: /Share/ }).first().click();
    await settle(page);
    await shot(page, 'topology-share');
  });

  test('lab status', async ({ page, api }) => {
    await page.goto('/status');
    await settle(page);
    await shot(page, 'lab-status');
  });

  test('phone', async ({ page, api }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/reservation');
    await settle(page);
    await shot(page, 'phone-reservation');
    await page.getByRole('button', { name: /Open main menu/ }).click();
    await settle(page);
    await page.screenshot({ path: `screenshots/${prefix}-phone-menu.png` });
  });
});
