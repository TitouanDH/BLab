// The Reservation page (#32) on the mocked API (mock-api.js): one state per Switch, one
// action per state, the filters, and no flicker from the 2 s poll. The fake lab: alice holds
// 10.69.145.12, bob holds .13 and shares his Topology with alice, .14 is in Quarantine,
// .15 is Out of service, .11 is free.
import { test, expect } from './fixtures.js';

const card = (page, ip) => page.locator(`[data-switch="${ip}"]`);
const state = (page, ip) => card(page, ip).getByTestId('state');

test('reserve a free Switch, see it as mine, Release it', async ({ page, api }) => {
  await page.goto('/reservation');
  const free = card(page, '10.69.145.11');
  await expect(state(page, '10.69.145.11')).toHaveText('Free');
  await free.getByRole('button', { name: 'Reserve' }).click();

  const dialog = page.getByRole('dialog', { name: /Reserve OS6860E-24/ });
  await expect(dialog.getByLabel('End date')).toHaveValue(/\d{4}-\d{2}-\d{2}/);
  await dialog.getByRole('button', { name: 'Reserve' }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole('status').filter({ hasText: 'Reservation successful.' })).toBeVisible();

  // Mine, and it stays in the list
  await expect(state(page, '10.69.145.11')).toHaveText('Reserved by me');
  await expect(free).toContainText(/Until .+ \(in 7 days\)/);
  await page.getByRole('button', { name: /^Mine/ }).click();
  await expect(free).toBeVisible();
  await expect(card(page, '10.69.145.13')).toBeHidden();

  await free.getByRole('button', { name: 'Release' }).click();
  const confirm = page.getByRole('dialog', { name: 'Release this Switch?' });
  await expect(confirm).toContainText('Cleans it up');
  await confirm.getByRole('button', { name: 'Release' }).click();
  await expect(confirm).toBeHidden();
  await expect(page.getByRole('status').filter({ hasText: /^Released\./ })).toBeVisible();
  expect(api.state.reservations.some(r => r.switch === 1)).toBe(false);

  // Pending while the Cleanup runs, then free again
  await page.getByRole('button', { name: /^All/ }).click();
  await expect(state(page, '10.69.145.11')).toHaveText('Being Cleaned up');
  await expect(free.getByRole('button')).toHaveCount(0);
  api.state.cleaningUp = [];
  await expect(free.getByRole('button', { name: 'Reserve' })).toBeVisible({ timeout: 5000 });
});

test('each card shows one state and the action it allows', async ({ page, api }) => {
  await page.goto('/reservation');

  await expect(state(page, '10.69.145.11')).toHaveText('Free');
  await expect(card(page, '10.69.145.11').getByRole('button')).toHaveText(['Reserve']);

  const mine = card(page, '10.69.145.12');
  await expect(state(page, '10.69.145.12')).toHaveText('Reserved by me');
  await expect(mine.getByRole('button')).toHaveText(['Release', 'Renew']);
  await expect(mine).toContainText('2 Renewals left');

  // Bob shares his Topology with alice: she may Renew or Release it too
  const shared = card(page, '10.69.145.13');
  await expect(state(page, '10.69.145.13')).toHaveText('Reserved by bob');
  await expect(shared).toContainText('bob shares their Topology with you');
  await expect(shared.getByRole('button')).toHaveText(['Release', 'Renew']);

  const quarantined = card(page, '10.69.145.14');
  await expect(state(page, '10.69.145.14')).toHaveText('Quarantine');
  await expect(quarantined).toContainText('Unwanted cable on 1/1/5. Named: carol.');
  await expect(quarantined.getByRole('button')).toHaveCount(0);
  await expect(quarantined.getByRole('link', { name: 'Re-check on Lab status' })).toHaveAttribute('href', '/status');

  const broken = card(page, '10.69.145.15');
  await expect(state(page, '10.69.145.15')).toHaveText('Out of service');
  await expect(broken).toContainText('PSU broken');
  await expect(broken.getByRole('button')).toHaveCount(0);
});

test('a Switch held by someone who does not share says when it frees up, with no action', async ({ page, api }) => {
  api.state.shares = [];
  await page.goto('/reservation');
  const other = card(page, '10.69.145.13');
  await expect(state(page, '10.69.145.13')).toHaveText('Reserved by bob');
  await expect(other).toContainText('It can be reserved once bob Releases it or the Reservation ends.');
  await expect(other.getByRole('button')).toHaveCount(0);
});

test('a held Switch an admin took out of service keeps its holder and says why', async ({ page, api }) => {
  api.state.switches[1].unavailable = { state: 'out_of_service', reason: 'Out of service: fan failure' };
  await page.goto('/reservation');
  await expect(state(page, '10.69.145.12')).toHaveText('Reserved by me');
  await expect(card(page, '10.69.145.12')).toContainText('Out of service: fan failure');
  await expect(card(page, '10.69.145.12').getByRole('button', { name: 'Release' })).toBeVisible();
});

test('filters: free only, mine, and search', async ({ page, api }) => {
  await page.goto('/reservation');
  await expect(page.locator('[data-switch]')).toHaveCount(5);

  await page.getByRole('button', { name: 'Free (1)' }).click();
  await expect(page.locator('[data-switch]')).toHaveCount(1);
  await expect(card(page, '10.69.145.11')).toBeVisible();

  await page.getByRole('button', { name: 'Mine (1)' }).click();
  await expect(page.locator('[data-switch]')).toHaveCount(1);
  await expect(card(page, '10.69.145.12')).toBeVisible();

  await page.getByRole('button', { name: 'All (5)' }).click();
  await page.getByLabel('Search Switches').fill('bob');
  await expect(page.locator('[data-switch]')).toHaveCount(1);
  await expect(card(page, '10.69.145.13')).toBeVisible();

  await page.getByLabel('Search Switches').fill('nothing like this');
  await expect(page.getByText('No Switch matches "nothing like this".')).toBeVisible();
});

test('Renew a Reservation', async ({ page, api }) => {
  await page.goto('/reservation');
  const mine = card(page, '10.69.145.12');
  await mine.getByRole('button', { name: 'Renew' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Renewed for another 7 days. Renewals left: 1.' })).toBeVisible();
  await expect(mine).toContainText('1 Renewal left');
});

test('the 2 s poll never flickers, and makes no lookup per Reservation', async ({ page, api }) => {
  // A slow Reservation list: the Switch list answers first
  api.override('GET', 'list_reservation/', async () => { await new Promise(r => setTimeout(r, 400)); });
  await page.goto('/reservation');
  await expect(state(page, '10.69.145.12')).toHaveText('Reserved by me');

  // Every change to the cards' states and to the list, over a few polls
  await page.evaluate(() => {
    window.__changes = [];
    new MutationObserver(records => {
      for (const r of records) window.__changes.push(r.type);
    }).observe(document.querySelector('[data-switch]').parentElement, { subtree: true, childList: true, attributes: true, characterData: true });
  });
  await page.waitForTimeout(4500);
  expect(await page.evaluate(() => window.__changes)).toEqual([]);
  expect(api.calls.filter(c => c.startsWith('GET list_reservation/')).length).toBeGreaterThanOrEqual(3);
  expect(api.calls.filter(c => c.startsWith('GET list_user'))).toEqual([]);
});

test('Lab status uses the same states and date format', async ({ page, api }) => {
  await page.goto('/status');
  await expect(page.locator('td[data-switch="10.69.145.11"]')).toContainText('Free');
  await expect(page.locator('td[data-switch="10.69.145.12"]')).toContainText('Reserved by me');
  await expect(page.locator('td[data-switch="10.69.145.13"]')).toContainText('Reserved by bob');
  await expect(page.locator('td[data-switch="10.69.145.14"]')).toContainText('Quarantine');
  await expect(page.locator('td[data-switch="10.69.145.15"]')).toContainText('Out of service');
  await expect(page.getByRole('row', { name: /10\.69\.145\.12/ })).toContainText('(in 4 days)');
});
