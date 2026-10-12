// The Reservation page (#32, #37) on the mocked API (mock-api.js): a table grouped by model
// by default (cards as an option), one state per Switch, the actions it allows, "free on"
// dates, cabled ports, one-click Reserve, the filters, and no flicker from the 2 s poll. The
// fake lab: alice holds Switch 2, bob holds 3 and shares his Topology with alice, 4 is in
// Quarantine, 5 is Out of service, 1 is free. Rows and cards are keyed by Switch id: only
// alice's and bob's come with their IP.
import { test, expect } from './fixtures.js';

const DAY = 24 * 60 * 60 * 1000;
const card = (page, id) => page.locator(`[data-switch="${id}"]`);
const state = (page, id) => card(page, id).getByTestId('state');
const reserveButton = (page, id) => card(page, id).getByRole('button', { name: 'Reserve', exact: true });
const viewButton = (page, name) => page.getByRole('group', { name: 'View' }).getByRole('button', { name });
// The ids of a model's rows, top to bottom
const rowOrder = (page, model) => page.locator(`tbody[data-model="${model}"] tr[data-switch]`)
  .evaluateAll(rows => rows.map(r => r.dataset.switch));

// Two more OS6860E-24: 6 is free, 7 is carol's until 3 days from now, with no Port cabled
const moreOS6860 = (api) => {
  api.state.switches.push(
    { id: 6, model: 'OS6860E-24', mngt_IP: '10.69.145.16', console: 'ts1:2006', part_number: '903624-90', hardware_revision: '05', serial_number: 'SN0006', unavailable: null },
    { id: 7, model: 'OS6860E-24', mngt_IP: '10.69.145.17', console: 'ts1:2007', part_number: '903624-90', hardware_revision: '05', serial_number: 'SN0007', unavailable: null },
  );
  api.state.ports.push({ id: 61, switch: 6, port_switch: '1/1/1', backbone: '10.69.144.1', port_backbone: '1/1/61', svlan: null, status: 'UP' });
  api.state.reservations.push({ id: 7, switch: 7, user: 3, creation_date: new Date().toISOString(),
    end_date: new Date(Date.now() + 3 * DAY).toISOString(), renewals_left: 2, admin_exception: false });
};

test('reserve a free Switch, see it as mine, Release it', async ({ page, api }) => {
  await page.goto('/reservation');
  const free = card(page, '1');
  await expect(state(page, '1')).toHaveText('Free');
  await free.getByRole('button', { name: 'Reserve until…' }).click();

  const dialog = page.getByRole('dialog', { name: /Reserve OS6860E-24/ });
  await expect(dialog.getByLabel('End date')).toHaveValue(/\d{4}-\d{2}-\d{2}/);
  await dialog.getByRole('button', { name: 'Reserve' }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole('status').filter({ hasText: 'Reservation successful.' })).toBeVisible();

  // Mine, and it stays in the list
  await expect(state(page, '1')).toHaveText('Reserved by me');
  await expect(free.getByTestId('free-on')).toContainText('(in 7 days)');
  await page.getByRole('button', { name: /^Mine/ }).click();
  await expect(free).toBeVisible();
  await expect(card(page, '3')).toBeHidden();

  await free.getByRole('button', { name: 'Release' }).click();
  const confirm = page.getByRole('dialog', { name: 'Release this Switch?' });
  await expect(confirm).toContainText('Cleans it up');
  await confirm.getByRole('button', { name: 'Release' }).click();
  await expect(confirm).toBeHidden();
  await expect(page.getByRole('status').filter({ hasText: /^Released\./ })).toBeVisible();
  expect(api.state.reservations.some(r => r.switch === 1)).toBe(false);

  // Pending while the Cleanup runs, then free again
  await page.getByRole('button', { name: /^All/ }).click();
  await expect(state(page, '1')).toHaveText('Being Cleaned up');
  await expect(free.getByRole('button')).toHaveCount(0);
  api.state.cleaningUp = [];
  await expect(reserveButton(page, '1')).toBeVisible({ timeout: 5000 });
});

test('each row shows one state, when it frees up, and the actions it allows', async ({ page, api }) => {
  await page.goto('/reservation');

  await expect(state(page, '1')).toHaveText('Free');
  await expect(card(page, '1').getByTestId('free-on')).toHaveText('Now');
  await expect(card(page, '1').getByRole('button')).toHaveText(['Reserve', 'Reserve until…']);

  const mine = card(page, '2');
  await expect(state(page, '2')).toHaveText('Reserved by me');
  await expect(mine.getByTestId('free-on')).toContainText('(in 4 days)');
  await expect(mine.getByTestId('free-on')).toContainText('2 Renewals left');
  await expect(mine.getByRole('button')).toHaveText(['Release', 'Renew']);

  // Bob shares his Topology with alice: she may Renew or Release it too
  const shared = card(page, '3');
  await expect(state(page, '3')).toHaveText('Reserved by bob');
  await expect(shared.getByTestId('free-on')).toContainText('(in 1 week)');
  await expect(shared.getByRole('button')).toHaveText(['Release', 'Renew']);

  const quarantined = card(page, '4');
  await expect(state(page, '4')).toHaveText('Quarantine');
  await expect(quarantined.getByTestId('free-on')).toHaveText('In Quarantine: Unwanted cable on 1/1/5. Named: carol.');
  await expect(quarantined.getByRole('button')).toHaveCount(0);
  await expect(quarantined.getByRole('link', { name: 'Re-check on Lab status' })).toHaveAttribute('href', '/status');

  const broken = card(page, '5');
  await expect(state(page, '5')).toHaveText('Out of service');
  await expect(broken.getByTestId('free-on')).toHaveText('Out of service: PSU broken');
  await expect(broken.getByRole('button')).toHaveCount(0);
});

test('the table is grouped by model, each with how many are free, and shows cabled ports', async ({ page, api }) => {
  moreOS6860(api);
  await page.goto('/reservation');
  const groups = page.locator('tbody[data-model]');
  await expect(groups).toHaveCount(5);
  await expect(groups.first()).toHaveAttribute('data-model', 'OS6360-10');
  const os6860 = page.locator('tbody[data-model="OS6860E-24"]');
  await expect(os6860.getByTestId('free-count')).toHaveText('2 free of 3');
  await expect(page.locator('tbody[data-model="OS6360-10"]').getByTestId('free-count')).toHaveText('0 free of 1');
  // Free first, then the soonest free: carol's ends in 3 days
  expect(await rowOrder(page, 'OS6860E-24')).toEqual(['1', '6', '7']);
  await expect(card(page, '7').getByTestId('free-on')).toContainText('(in 3 days)');
  await expect(card(page, '7').getByRole('button')).toHaveCount(0);
  // Cabled ports: the Ports paired with a UNI, whoever holds the Switch
  await expect(card(page, '1').getByTestId('cabled-ports')).toHaveText('2');
  await expect(card(page, '6').getByTestId('cabled-ports')).toHaveText('1');
  await expect(card(page, '7').getByTestId('cabled-ports')).toHaveText('0');
});

test('every column sorts, both ways, within each model', async ({ page, api }) => {
  moreOS6860(api);
  await page.goto('/reservation');
  const order = () => rowOrder(page, 'OS6860E-24');
  const header = (name) => page.getByRole('columnheader', { name });
  await expect(header('Free on')).toHaveAttribute('aria-sort', 'ascending');
  await expect(card(page, '1')).toBeVisible();

  await header('Cabled ports').getByRole('button').click();
  await expect(header('Cabled ports')).toHaveAttribute('aria-sort', 'ascending');
  await expect(header('Free on')).toHaveAttribute('aria-sort', 'none');
  expect(await order()).toEqual(['7', '6', '1']);
  await header('Cabled ports').getByRole('button').click();
  await expect(header('Cabled ports')).toHaveAttribute('aria-sort', 'descending');
  expect(await order()).toEqual(['1', '6', '7']);

  await header('Switch').getByRole('button').click();
  expect(await order()).toEqual(['1', '6', '7']);
  await header('Switch').getByRole('button').click();
  expect(await order()).toEqual(['7', '6', '1']);

  await header('State').getByRole('button').click();
  expect(await order()).toEqual(['1', '6', '7']);
  await header('State').getByRole('button').click();
  expect(await order()).toEqual(['7', '1', '6']);

  await header('Free on').getByRole('button').click();
  await header('Free on').getByRole('button').click();
  expect(await order()).toEqual(['7', '1', '6']);
  // The models stay in alphabetical order
  await expect(page.locator('tbody[data-model]').first()).toHaveAttribute('data-model', 'OS6360-10');
});

test('one click on Reserve reserves for 7 days, with no dialog', async ({ page, api }) => {
  await page.goto('/reservation');
  await reserveButton(page, '1').click();
  await expect(page.getByRole('status').filter({ hasText: /Reservation successful\. OS6860E-24 #1 is yours until/ })).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(state(page, '1')).toHaveText('Reserved by me');
  // The end of the 7th day from today
  const end = new Date(api.state.reservations.find(r => r.switch === 1).end_date);
  const expected = new Date();
  expected.setDate(expected.getDate() + 7);
  expected.setHours(23, 59, 59, 0);
  expect(end.getTime()).toBe(expected.getTime());
});

test('Reserve until… opens the date dialog on the default day', async ({ page, api }) => {
  await page.goto('/reservation');
  await card(page, '1').getByRole('button', { name: 'Reserve until…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Reserve OS6860E-24 #1' });
  const day = new Date();
  day.setDate(day.getDate() + 7);
  const pad = (n) => String(n).padStart(2, '0');
  await expect(dialog.getByLabel('End date')).toHaveValue(`${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`);
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  await expect(dialog).toBeHidden();
  expect(api.calls.filter(c => c === 'POST reserve/')).toEqual([]);
});

test('cards are an option, grouped the same way, and the choice is remembered', async ({ page, api }) => {
  moreOS6860(api);
  await page.goto('/reservation');
  await expect(page.locator('table')).toBeVisible();
  await viewButton(page, 'Cards').click();
  await expect(page.locator('table')).toHaveCount(0);
  const os6860 = page.getByRole('region', { name: 'OS6860E-24' });
  await expect(os6860.getByTestId('free-count')).toHaveText('2 free of 3');
  await expect(os6860.locator('article[data-switch]')).toHaveCount(3);
  await expect(card(page, '1')).toContainText('2 cabled ports');
  await expect(card(page, '7')).toContainText('Free on');
  await expect(card(page, '1').getByRole('button')).toHaveText(['Reserve', 'Reserve until…']);
  await expect(card(page, '2').getByRole('button')).toHaveText(['Release', 'Renew']);

  // One click reserves from a card too
  await reserveButton(page, '6').click();
  await expect(state(page, '6')).toHaveText('Reserved by me');

  await page.reload();
  await expect(page.locator('article[data-switch="1"]')).toBeVisible();
  await expect(viewButton(page, 'Cards')).toHaveAttribute('aria-pressed', 'true');
  await viewButton(page, 'Table').click();
  await page.reload();
  await expect(page.locator('tr[data-switch="1"]')).toBeVisible();
});

test('a Switch held by someone who does not share says when it frees up, with no action', async ({ page, api }) => {
  api.state.shares = [];
  await page.goto('/reservation');
  await viewButton(page, 'Cards').click();
  const other = card(page, '3');
  await expect(state(page, '3')).toHaveText('Reserved by bob');
  await expect(other).toContainText(/Free on .+ \(in 1 week\)/);
  await expect(other).toContainText('It can be reserved once bob Releases it or the Reservation ends.');
  await expect(other.getByRole('button')).toHaveCount(0);
});

test('a held Switch an admin took out of service keeps its holder and says why', async ({ page, api }) => {
  api.state.switches[1].unavailable = { state: 'out_of_service', reason: 'Out of service: fan failure' };
  await page.goto('/reservation');
  await expect(state(page, '2')).toHaveText('Reserved by me');
  await expect(card(page, '2').getByTestId('free-on')).toContainText('Out of service: fan failure');
  await expect(card(page, '2').getByRole('button', { name: 'Release' })).toBeVisible();
});

test('filters: free only, mine, and search', async ({ page, api }) => {
  await page.goto('/reservation');
  await expect(page.locator('[data-switch]')).toHaveCount(5);

  await page.getByRole('button', { name: 'Free (1)' }).click();
  await expect(page.locator('[data-switch]')).toHaveCount(1);
  await expect(card(page, '1')).toBeVisible();

  await page.getByRole('button', { name: 'Mine (1)' }).click();
  await expect(page.locator('[data-switch]')).toHaveCount(1);
  await expect(card(page, '2')).toBeVisible();

  await page.getByRole('button', { name: 'All (5)' }).click();
  await page.getByLabel('Search Switches').fill('bob');
  await expect(page.locator('[data-switch]')).toHaveCount(1);
  await expect(card(page, '3')).toBeVisible();

  await page.getByLabel('Search Switches').fill('nothing like this');
  await expect(page.getByText('No Switch matches "nothing like this".')).toBeVisible();
});

test('Renew a Reservation', async ({ page, api }) => {
  await page.goto('/reservation');
  const mine = card(page, '2');
  await mine.getByRole('button', { name: 'Renew' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Renewed for another 7 days. Renewals left: 1.' })).toBeVisible();
  await expect(mine).toContainText('1 Renewal left');
});

test('the 2 s poll never flickers, and makes no lookup per Reservation', async ({ page, api }) => {
  // A slow Reservation list: the Switch list answers first
  api.override('GET', 'list_reservation/', async () => { await new Promise(r => setTimeout(r, 400)); });
  await page.goto('/reservation');
  await expect(state(page, '2')).toHaveText('Reserved by me');

  // Every change to the rows' states and to the table, over a few polls
  await page.evaluate(() => {
    window.__changes = [];
    new MutationObserver(records => {
      for (const r of records) window.__changes.push(r.type);
    }).observe(document.querySelector('[data-switch]').closest('table'), { subtree: true, childList: true, attributes: true, characterData: true });
  });
  await page.waitForTimeout(4500);
  expect(await page.evaluate(() => window.__changes)).toEqual([]);
  expect(api.calls.filter(c => c.startsWith('GET list_reservation/')).length).toBeGreaterThanOrEqual(3);
  expect(api.calls.filter(c => c.startsWith('GET list_user'))).toEqual([]);
});

test('Lab status uses the same states and date format', async ({ page, api }) => {
  await page.goto('/status');
  await expect(page.locator('td[data-switch="1"]')).toContainText('Free');
  await expect(page.locator('td[data-switch="2"]')).toContainText('Reserved by me');
  await expect(page.locator('td[data-switch="3"]')).toContainText('Reserved by bob');
  await expect(page.locator('td[data-switch="4"]')).toContainText('Quarantine');
  await expect(page.locator('td[data-switch="5"]')).toContainText('Out of service');
  await expect(page.getByRole('row', { name: /10\.69\.145\.12/ })).toContainText('(in 4 days)');
});

test('a Switch alice may not work on comes by model and id, without its IP or hardware (#36)', async ({ page, api }) => {
  await page.goto('/reservation');
  // Her own and bob's (shared with her): with the IP, in the table and on the cards
  await expect(card(page, '2')).toContainText('10.69.145.12');
  await expect(card(page, '3')).toContainText('10.69.145.13');
  for (const id of ['1', '4']) {
    await expect(card(page, id)).toContainText(`#${id}`);
    await expect(card(page, id)).not.toContainText('10.69.145.');
  }
  // The cards also give the Details
  await viewButton(page, 'Cards').click();
  await card(page, '3').getByText('Details').click();
  await expect(card(page, '3')).toContainText('SN0003');
  // A free one, and carol's Quarantined one: only model and id
  for (const id of ['1', '4']) {
    await expect(card(page, id)).toContainText(`#${id}`);
    await expect(card(page, id)).not.toContainText('10.69.145.');
    await expect(card(page, id).getByText('Details')).toHaveCount(0);
  }
  await page.getByLabel('Search Switches').fill('#4');
  await expect(page.locator('[data-switch]')).toHaveCount(1);
  await expect(card(page, '4')).toBeVisible();
});

test('on a phone the page opens on cards until the user chooses', async ({ page, api }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/reservation');
  await expect(page.locator('article[data-switch="1"]')).toBeVisible();
  await viewButton(page, 'Table').click();
  await page.reload();
  await expect(page.locator('tr[data-switch="1"]')).toBeVisible();
});
