// The Reservation page (#32, #37, #53) on the mocked API (mock-api.js): a sidebar of model
// families and models with a minimum of cabled ports, next to a compact table (cards as an
// option), one state per Switch, Reserve + a ⋯ menu for the other actions, "free on" dates,
// cabled ports, one-click Reserve, the filters, and no flicker from the 2 s poll. The fake
// lab: alice holds Switch 2, bob holds 3 and shares his Topology with alice, 4 is in
// Quarantine, 5 is Out of service, 1 is free. Rows and cards are keyed by Switch id: only
// alice's and bob's come with their IP.
import { test, expect } from './fixtures.js';

const DAY = 24 * 60 * 60 * 1000;
const card = (page, id) => page.locator(`[data-switch="${id}"]`);
const state = (page, id) => card(page, id).getByTestId('state');
const reserveButton = (page, id) => card(page, id).getByRole('button', { name: 'Reserve', exact: true });
const moreButton = (page, id) => card(page, id).getByRole('button', { name: /^More actions for/ });
const viewButton = (page, name) => page.getByRole('group', { name: 'View' }).getByRole('button', { name });
const sidebar = (page) => page.getByRole('complementary', { name: 'Models and cabled ports' });
// The ⋯ menu's entries for a Switch (opens it, and waits for it to take the focus, so Escape closes it)
const menu = async (page, id) => {
  await moreButton(page, id).click();
  await expect(page.getByRole('menu')).toBeFocused();
  return page.getByRole('menuitem');
};
const closeMenu = async (page) => {
  await page.keyboard.press('Escape');
  await expect(page.getByRole('menu')).toHaveCount(0);
};
// The ids of the table's rows, top to bottom
const rowOrder = (page) => page.locator('tbody tr[data-switch]').evaluateAll(rows => rows.map(r => r.dataset.switch));

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

test('reserve a free Switch until a chosen day, see it as mine, Release it', async ({ page, api }) => {
  await page.goto('/reservation');
  const free = card(page, '1');
  await expect(state(page, '1')).toHaveText('Free');
  await (await menu(page, '1')).filter({ hasText: 'Reserve until…' }).click();

  const dialog = page.getByRole('dialog', { name: /Reserve OS6860E-24/ });
  await expect(dialog.getByLabel('End date')).toHaveValue(/\d{4}-\d{2}-\d{2}/);
  await dialog.getByRole('button', { name: 'Reserve' }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole('status').filter({ hasText: 'Reservation successful.' })).toBeVisible();

  // Mine, set apart from the other rows, and it stays in the list
  await expect(state(page, '1')).toHaveText('Reserved by me');
  await expect(free).toHaveClass(/bg-primary-50/);
  await expect(free.getByTestId('free-on')).toContainText('in 7 days');
  await page.getByRole('button', { name: /^Mine/ }).click();
  await expect(free).toBeVisible();
  await expect(card(page, '3')).toBeHidden();

  await (await menu(page, '1')).filter({ hasText: 'Release' }).click();
  const confirm = page.getByRole('dialog', { name: 'Release this Switch?' });
  await expect(confirm).toContainText('Cleans it up');
  await confirm.getByRole('button', { name: 'Release' }).click();
  await expect(confirm).toBeHidden();
  await expect(page.getByRole('status').filter({ hasText: /^Released\./ })).toBeVisible();
  expect(api.state.reservations.some(r => r.switch === 1)).toBe(false);

  // Pending while the Cleanup runs, with no action, then free again
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
  await expect(reserveButton(page, '1')).toBeVisible();
  await expect(await menu(page, '1')).toHaveText([/^Reserve until…/]);
  await closeMenu(page);

  const mine = card(page, '2');
  await expect(state(page, '2')).toHaveText('Reserved by me');
  await expect(mine.getByTestId('free-on')).toContainText('in 4 days');
  await expect(mine.getByTestId('free-on')).toContainText('2 Renewals left');
  await expect(reserveButton(page, '2')).toHaveCount(0);
  await expect(await menu(page, '2')).toHaveText(['Renew +7 days, 2 Renewals left', /^Release/]);
  await closeMenu(page);

  // Bob shares his Topology with alice: she may Renew or Release it too
  await expect(state(page, '3')).toHaveText('Reserved by bob');
  await expect(card(page, '3').getByTestId('free-on')).toContainText('in 1 week');
  await expect(await menu(page, '3')).toHaveText([/^Renew/, /^Release/]);
  await closeMenu(page);

  // Quarantine: the ⋯ menu leads to Lab status to Re-check it
  await expect(state(page, '4')).toHaveText('Quarantine');
  await expect(card(page, '4').getByTestId('free-on')).toHaveText('In Quarantine: Unwanted cable on 1/1/5. Named: carol.');
  await expect(reserveButton(page, '4')).toHaveCount(0);

  const broken = card(page, '5');
  await expect(state(page, '5')).toHaveText('Out of service');
  await expect(broken.getByTestId('free-on')).toHaveText('Out of service: PSU broken');
  await expect(broken.getByRole('button')).toHaveCount(0);

  await (await menu(page, '4')).filter({ hasText: 'Re-check on Lab status' }).click();
  await expect(page).toHaveURL(/\/status$/);
});

test('the sidebar lists families and models with how many are free, and narrows the table', async ({ page, api }) => {
  moreOS6860(api);
  await page.goto('/reservation');
  const side = sidebar(page);
  await expect(side.locator('[data-family]')).toHaveText([/^OS6360/, /^OS6465/, /^OS6560/, /^OS6860E/, /^OS6900/]);
  await expect(side.locator('[data-family="OS6860E"] > button').getByTestId('free-count')).toHaveText('2/3');
  await expect(side.locator('[data-model="OS6860E-24"]')).toContainText('-24');
  await expect(side.locator('[data-model="OS6360-10"]').getByTestId('free-count')).toHaveText('0/1');
  await expect(page.locator('tbody tr[data-switch]')).toHaveCount(7);

  // One model
  await side.getByRole('checkbox', { name: 'OS6900-X20' }).check();
  expect(await rowOrder(page)).toEqual(['2']);
  await expect(page.getByTestId('row-count')).toHaveText('1 Switch · OS6900-X20');
  // And a whole family: a click on its name
  await side.getByRole('button', { name: /^OS6860E/ }).click();
  expect(await rowOrder(page)).toEqual(['1', '6', '7', '2']);
  // The free counts don't change with the filters
  await expect(side.locator('[data-model="OS6860E-24"]').getByTestId('free-count')).toHaveText('2/3');

  // At least 1 cabled port: carol's 7 has none
  await side.getByRole('slider').fill('1');
  await expect(side.getByText('At least 1 cabled port', { exact: true })).toBeVisible();
  expect(await rowOrder(page)).toEqual(['1', '6', '2']);

  // The sidebar's Clear unpicks the models; the one above the table also resets the ports
  await side.getByRole('button', { name: 'Clear', exact: true }).click();
  expect(await rowOrder(page)).toEqual(['1', '6', '2', '3']);
  await page.getByTestId('row-count').locator('..').getByRole('button', { name: 'Clear' }).click();
  await expect(page.locator('tbody tr[data-switch]')).toHaveCount(7);

  // Nothing left: says why, and offers to show them all again
  await side.getByRole('checkbox', { name: 'OS6360-10' }).check();
  await page.getByRole('button', { name: /^Free \(/ }).click();
  await expect(page.getByText('No Switch with OS6360-10 among the free ones.')).toBeVisible();
  await page.getByRole('button', { name: 'Show every model' }).click();
  await expect(page.locator('tbody tr[data-switch]')).toHaveCount(2);
});

test('the table is one row per Switch, free first then the soonest free, with cabled ports', async ({ page, api }) => {
  moreOS6860(api);
  await page.goto('/reservation');
  // Free now (by model), then by the end of the Reservation, then what can't be reserved
  expect(await rowOrder(page)).toEqual(['1', '6', '7', '2', '3', '5', '4']);
  await expect(card(page, '7').getByTestId('free-on')).toContainText('in 3 days');
  await expect(card(page, '7').getByRole('button')).toHaveCount(0);
  // Cabled ports: the Ports paired with a UNI, whoever holds the Switch
  await expect(card(page, '1').getByTestId('cabled-ports')).toHaveText('2');
  await expect(card(page, '6').getByTestId('cabled-ports')).toHaveText('1');
  await expect(card(page, '7').getByTestId('cabled-ports')).toHaveText('0');
});

test('every column sorts, both ways', async ({ page, api }) => {
  moreOS6860(api);
  await page.goto('/reservation');
  const header = (name) => page.getByRole('columnheader', { name });
  await expect(header('Free on')).toHaveAttribute('aria-sort', 'ascending');
  await expect(card(page, '1')).toBeVisible();
  const os6860 = async () => (await rowOrder(page)).filter(id => ['1', '6', '7'].includes(id));

  await header('Cabled ports').getByRole('button').click();
  await expect(header('Cabled ports')).toHaveAttribute('aria-sort', 'ascending');
  await expect(header('Free on')).toHaveAttribute('aria-sort', 'none');
  expect(await os6860()).toEqual(['7', '6', '1']);
  await header('Cabled ports').getByRole('button').click();
  await expect(header('Cabled ports')).toHaveAttribute('aria-sort', 'descending');
  expect(await os6860()).toEqual(['1', '6', '7']);

  // By model, then id
  await header('Switch').getByRole('button').click();
  expect(await rowOrder(page)).toEqual(['5', '4', '3', '1', '6', '7', '2']);
  await header('Switch').getByRole('button').click();
  expect(await rowOrder(page)).toEqual(['2', '7', '6', '1', '3', '4', '5']);

  await header('State').getByRole('button').click();
  expect(await os6860()).toEqual(['1', '6', '7']);
  await header('State').getByRole('button').click();
  expect(await os6860()).toEqual(['7', '1', '6']);
});

test('one click on Reserve reserves for 7 days, with no dialog', async ({ page, api }) => {
  await page.goto('/reservation');
  await reserveButton(page, '1').click();
  await expect(page.getByRole('status').filter({ hasText: /Reservation successful\. OS6860E-24 10.69.145.11 is yours until/ })).toBeVisible();
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
  await (await menu(page, '1')).filter({ hasText: 'Reserve until…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Reserve OS6860E-24 10.69.145.11' });
  const day = new Date();
  day.setDate(day.getDate() + 7);
  const pad = (n) => String(n).padStart(2, '0');
  await expect(dialog.getByLabel('End date')).toHaveValue(`${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`);
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  await expect(dialog).toBeHidden();
  expect(api.calls.filter(c => c === 'POST reserve/')).toEqual([]);
});

test('cards are an option, grouped by family, narrowed by the sidebar, and the choice is remembered', async ({ page, api }) => {
  moreOS6860(api);
  await page.goto('/reservation');
  await expect(page.locator('table')).toBeVisible();
  await viewButton(page, 'Cards').click();
  await expect(page.locator('table')).toHaveCount(0);
  const os6860 = page.getByRole('region', { name: 'OS6860E' });
  await expect(os6860.getByTestId('free-count')).toHaveText('2 free of 3');
  await expect(os6860.locator('article[data-switch]')).toHaveCount(3);
  await expect(card(page, '1').getByTestId('cabled-ports')).toHaveText('2');
  await expect(card(page, '7')).toContainText('Free on');
  await expect(card(page, '2')).toContainText('Until');
  await expect(card(page, '2')).toHaveClass(/ring-primary-600/);
  await expect(await menu(page, '2')).toHaveText([/^Renew/, /^Release/]);
  await closeMenu(page);

  // One click reserves from a card too
  await reserveButton(page, '6').click();
  await expect(state(page, '6')).toHaveText('Reserved by me');

  await sidebar(page).getByRole('checkbox', { name: 'OS6900-X20' }).check();
  await expect(page.locator('article[data-switch]')).toHaveCount(1);

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
  const other = card(page, '3');
  await expect(state(page, '3')).toHaveText('Reserved by bob');
  await expect(other.getByTestId('free-on')).toContainText('in 1 week');
  await expect(other.getByRole('button')).toHaveCount(0);
  await viewButton(page, 'Cards').click();
  await expect(card(page, '3')).toContainText('Free on');
  await expect(card(page, '3').getByRole('button')).toHaveCount(0);
});

test('a held Switch an admin took out of service keeps its holder and says why', async ({ page, api }) => {
  api.state.switches[1].unavailable = { state: 'out_of_service', reason: 'Out of service: fan failure' };
  await page.goto('/reservation');
  await expect(state(page, '2')).toHaveText('Reserved by me');
  await expect(card(page, '2').getByTestId('free-on')).toContainText('Out of service: fan failure');
  await expect((await menu(page, '2')).filter({ hasText: 'Release' })).toBeVisible();
  await closeMenu(page);
  // The card says why too
  await viewButton(page, 'Cards').click();
  await expect(card(page, '2').getByTestId('free-on')).toContainText('Out of service: fan failure');
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

test('no Renewals left: Renew is shown but does nothing', async ({ page, api }) => {
  api.state.reservations.find(r => r.switch === 2).renewals_left = 0;
  await page.goto('/reservation');
  const renew = (await menu(page, '2')).filter({ hasText: 'Renew' });
  await expect(renew).toContainText('No Renewals left');
  await expect(renew).toBeDisabled();
  await renew.click({ force: true });
  expect(api.calls.filter(c => c.startsWith('POST renew'))).toEqual([]);
});

test('Renew a Reservation', async ({ page, api }) => {
  await page.goto('/reservation');
  await (await menu(page, '2')).filter({ hasText: 'Renew' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Renewed for another 7 days. Renewals left: 1.' })).toBeVisible();
  await expect(card(page, '2').getByTestId('free-on')).toContainText('1 Renewal left');
});

test('the 2 s poll never flickers, and makes no lookup per Reservation', async ({ page, api }) => {
  // A slow Reservation list: the Switch list answers first
  api.override('GET', 'list_reservation/', async () => { await new Promise(r => setTimeout(r, 400)); });
  await page.goto('/reservation');
  await expect(state(page, '2')).toHaveText('Reserved by me');

  // Every change to the rows' states, to the table and to the sidebar, over a few polls
  await page.evaluate(() => {
    window.__changes = [];
    const observer = new MutationObserver(records => {
      for (const r of records) window.__changes.push(r.type);
    });
    for (const el of [document.querySelector('table'), document.querySelector('aside')]) {
      observer.observe(el, { subtree: true, childList: true, attributes: true, characterData: true });
    }
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

test('every Switch comes by model and IP, with its hardware, whoever holds it (#75)', async ({ page, api }) => {
  await page.goto('/reservation');
  // Hers, bob's (shared with her), and those she may not work on: with the IP, no id
  for (const id of ['1', '2', '3', '4']) {
    await expect(card(page, id)).toContainText(`10.69.145.1${id}`);
    await expect(card(page, id)).not.toContainText(`#${id}`);
  }
  // The cards also give the Details, for every Switch
  await viewButton(page, 'Cards').click();
  for (const id of ['1', '3', '4']) {
    await card(page, id).getByText('Details').click();
    await expect(card(page, id)).toContainText(`SN000${id}`);
    await expect(card(page, id)).not.toContainText(`#${id}`);
  }
  await page.getByLabel('Search Switches').fill('10.69.145.14');
  await expect(page.locator('[data-switch]')).toHaveCount(1);
  await expect(card(page, '4')).toBeVisible();
});

test('on a phone the page opens on cards, with the facets folded above them, until the user chooses', async ({ page, api }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/reservation');
  await expect(page.locator('article[data-switch="1"]')).toBeVisible();
  await expect(sidebar(page)).toBeHidden();
  await page.getByText('Models and cabled ports').click();
  await page.getByRole('checkbox', { name: 'OS6860E-24' }).check();
  await expect(page.locator('article[data-switch]')).toHaveCount(1);
  await viewButton(page, 'Table').click();
  await page.reload();
  await expect(page.locator('tr[data-switch="1"]')).toBeVisible();
});
