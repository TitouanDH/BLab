// My lab, the page after logging in (#31), on the mocked API (mock-api.js): Alice holds
// 10.69.145.12 (2 Renewals left); Bob shares his Topology with her.
import { test, expect } from './fixtures.js';

const row = (page, ip) => page.locator(`[data-switch="${ip}"]`);

test('My Reservations: Renew pushes the end date back and counts down', async ({ page, api }) => {
  await page.goto('/');
  const mine = row(page, '10.69.145.12');
  await expect(mine).toContainText('OS6900-X20');
  await expect(mine).toContainText('(in 4 days)');
  await expect(mine).toContainText('2 Renewals left');
  await mine.getByRole('button', { name: 'Renew' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Renewed for another 7 days. Renewals left: 1.' })).toBeVisible();
  await expect(mine).toContainText('1 Renewal left');
  await expect(mine).toContainText('(in 1 week)');
  expect(api.calls).toContain('POST renew/');
  // Bob's Switch is not mine
  await expect(row(page, '10.69.145.13')).toHaveCount(0);
});

test('a Reservation that cannot be Renewed says why instead', async ({ page, api }) => {
  api.state.reservations[0].renewals_left = 0;
  await page.goto('/');
  const mine = row(page, '10.69.145.12');
  await expect(mine).toContainText('No Renewals left: it has been Renewed 2 times.');
  await expect(mine.getByRole('button', { name: /Renew/ })).toHaveCount(0);
});

test('a refused Renewal shows the server reason', async ({ page, api }) => {
  api.override('POST', 'renew/', () => ({ status: 400, body: { detail: 'This Reservation has expired: it is being released.' } }));
  await page.goto('/');
  await row(page, '10.69.145.12').getByRole('button', { name: /Renew/ }).click();
  await expect(page.getByRole('alert')).toHaveText("Couldn't Renew this Reservation. This Reservation has expired: it is being released.");
});

test('Release from My lab', async ({ page, api }) => {
  await page.goto('/');
  await row(page, '10.69.145.12').getByRole('button', { name: 'Release' }).click();
  await page.getByRole('dialog', { name: 'Release this Switch?' }).getByRole('button', { name: 'Release' }).click();
  await expect(page.getByRole('status').filter({ hasText: /^Released\./ })).toBeVisible();
  await expect(page.getByTestId('empty-state')).toBeVisible();
});

test('a Quarantine naming me says what was found, that Reservations are blocked, and how to clear it', async ({ page, api }) => {
  Object.assign(api.state.quarantines[4], { holder: 'alice', holder_id: 1 });
  await page.goto('/');
  const notice = page.getByTestId('quarantine-notice');
  await expect(notice.getByRole('heading')).toHaveText('A Quarantine names you');
  await expect(notice).toContainText("you can't make new Reservations");
  await expect(row(page, '10.69.145.14')).toContainText('Unwanted cable on 1/1/5');
  await notice.getByRole('button', { name: 'Re-check' }).click();
  await expect(page.getByRole('alert')).toHaveText('Still not clean: Unwanted cable on 1/1/5.');
  expect(api.calls).toContain('POST recheck/');

  // Once an Inspection finds it clean, the notice goes
  delete api.state.quarantines[4];
  api.override('POST', 'recheck/', () => ({ status: 200, body: { clean: true, reasons: [], detail: 'Clean: the Quarantine is lifted.' } }));
  await notice.getByRole('button', { name: 'Re-check' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Clean: the Quarantine is lifted.' })).toBeVisible();
  await expect(notice).toBeHidden();
});

test('a Quarantine naming someone else is not shown', async ({ page, api }) => {
  await page.goto('/');
  await expect(row(page, '10.69.145.12')).toBeVisible();
  await expect(page.getByTestId('quarantine-notice')).toHaveCount(0);
});

test('my Topology in miniature links to the canvas', async ({ page, api }) => {
  await page.goto('/');
  const mini = page.getByTestId('topology-miniature');
  await expect(mini.locator('svg')).toContainText('SVLAN 1001');
  await expect(mini.locator('svg')).toContainText('10.69.145.13');  // the far end of the Link
  await mini.click();
  await expect(page).toHaveURL('/topology');
});

test('Shared with me opens that user\'s Topology', async ({ page, api }) => {
  await page.goto('/');
  await page.getByRole('link', { name: /bob's Topology/ }).click();
  await expect(page).toHaveURL('/topology?owner=2');
  await expect(page.getByLabel('Topology shown')).toHaveValue('2');
  await expect.poll(() => api.calls).toContain('GET topology/2/');
});

test('with nothing reserved, it points to Reservation', async ({ page, api }) => {
  api.state.reservations = api.state.reservations.filter(r => r.user !== 1);
  await page.goto('/');
  const empty = page.getByTestId('empty-state');
  await expect(empty).toContainText('You hold no Reservation.');
  await expect(page.getByRole('heading', { name: 'My Topology' })).toHaveCount(0);
  await empty.getByRole('link', { name: 'Reserve a Switch' }).click();
  await expect(page).toHaveURL('/reservation');
});

test.describe('logged out', () => {
  test.use({ loggedInAs: null });

  test('visitors go to the login page', async ({ page, api }) => {
    await page.goto('/');
    await expect(page).toHaveURL('/login');
  });
});

test('a Topology no longer shared, opened from a link, falls back to my own', async ({ page, api }) => {
  api.state.shares = [];
  await page.goto('/topology?owner=2');
  await expect(page.getByRole('alert')).toHaveText('This Topology is no longer shared with you.');
  await expect(page.getByLabel('Topology shown')).toHaveValue('1');
  await page.waitForTimeout(2500);
  expect(api.calls.filter(c => c === 'GET topology/2/').length).toBe(1);
});
