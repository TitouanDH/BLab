// Screenshots of every page against the mocked API, for the before/after pictures an
// issue's closing comment carries. Not part of `npm test`: run `npm run screenshots`,
// with SHOT_PREFIX naming the set (e.g. "before" or "after"). Files go to screenshots/.
import { test } from './fixtures.js';
import { clickElement, dragToConnect, waitForElement } from './canvas.js';

const prefix = process.env.SHOT_PREFIX || 'shot';
const shot = (page, name) => page.screenshot({ path: `screenshots/${prefix}-${name}.png`, fullPage: true });
const settle = (page) => page.waitForTimeout(800);  // polls answered, transitions done

test.describe('logged out @screenshot', () => {
  test.use({ loggedInAs: null });
  for (const [name, path] of [['login', '/login'], ['signup', '/signup']]) {
    test(name, async ({ page, api }) => {
      await page.goto(path);
      await settle(page);
      await shot(page, name);
    });
  }
});

test.describe('logged in @screenshot', () => {
  test('my lab', async ({ page, api }) => {
    await page.goto('/');
    await settle(page);
    await shot(page, 'my-lab');
  });

  test('my lab with a Quarantine naming me', async ({ page, api }) => {
    Object.assign(api.state.quarantines[4], { holder: 'alice', holder_id: 1 });
    api.state.reservations[0].renewals_left = 0;
    await page.goto('/');
    await settle(page);
    await shot(page, 'my-lab-quarantine');
  });

  test('my lab empty', async ({ page, api }) => {
    api.state.reservations = api.state.reservations.filter(r => r.user !== 1);
    await page.goto('/');
    await settle(page);
    await shot(page, 'my-lab-empty');
  });

  test('reservation', async ({ page, api }) => {
    // Two more OS6860E-24, one free and one carol's, so a model groups several Switches
    api.state.switches.push(
      { id: 6, model: 'OS6860E-24', unavailable: null, mngt_IP: '10.69.145.16' },
      { id: 7, model: 'OS6860E-24', unavailable: null, mngt_IP: '10.69.145.17' },
    );
    api.state.ports.push({ id: 61, switch: 6, port_switch: '1/1/1', backbone: '10.69.144.1', port_backbone: '1/1/61', svlan: null, status: 'UP' });
    api.state.reservations.push({ id: 7, switch: 7, user: 3, creation_date: new Date().toISOString(),
      end_date: new Date(Date.now() + 3 * 24 * 60 * 60 * 1000).toISOString(), renewals_left: 2, admin_exception: false });
    await page.goto('/reservation');
    await settle(page);
    await shot(page, 'reservation');
    await page.getByRole('button', { name: /^Free \(/ }).click();
    await settle(page);
    await shot(page, 'reservation-free');
    await page.getByRole('button', { name: /^All/ }).click();
    // Before #37 there were only cards, and Reserve opened the date dialog
    const cards = page.getByRole('button', { name: 'Cards' });
    if (await cards.count()) await cards.click();
    await settle(page);
    await shot(page, 'reservation-cards');
    const until = page.getByRole('button', { name: /^Reserve until/ });
    await ((await until.count()) ? until : page.getByRole('button', { name: /^Reserve$/ })).first().click();
    await settle(page);
    await shot(page, 'reservation-reserve-dialog');
  });

  test('reservation quick reserve', async ({ page, api }) => {
    await page.goto('/reservation');
    await page.getByRole('button', { name: /^Reserve$/ }).first().click();
    await settle(page);
    await shot(page, 'reservation-quick-reserve');
  });

  test('my lab with Switch accounts', async ({ page, api }) => {
    await page.goto('/#switch-accounts');
    await settle(page);
    await shot(page, 'my-lab-switch-accounts');
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
    // Before #37, Reserve opened the date dialog
    if (await page.getByRole('dialog').count()) await page.getByRole('dialog').getByRole('button', { name: /^Reserve$/ }).click();
    await settle(page);
    await shot(page, 'reservation-reserve-error');
  });

  test('topology', async ({ page, api }) => {
    // Alice also holds 10.69.145.11, ending tomorrow
    api.state.reservations.push({ id: 50, switch: 1, user: 1, creation_date: new Date().toISOString(),
      end_date: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString(), renewals_left: 2, admin_exception: false });
    await page.goto('/topology');
    await settle(page);
    await shot(page, 'topology-picker');
    await page.getByRole('link', { name: /My Topology/ }).click();
    await waitForElement(page, 'switch_3');
    await page.waitForTimeout(1500);
    await shot(page, 'topology');
    await page.getByRole('button', { name: 'Legend' }).click();
    await settle(page);
    await shot(page, 'topology-legend');
    await page.getByRole('button', { name: 'Legend' }).click();
    await dragToConnect(page, 'switch_2', 'switch_1');
    await settle(page);
    await shot(page, 'topology-connect');
    await page.keyboard.press('Escape');
    await clickElement(page, 'link_1001');
    await settle(page);
    await shot(page, 'topology-link');
    await clickElement(page, 'switch_2');
    await settle(page);
    await shot(page, 'topology-switch');
    await page.getByRole('complementary', { name: 'Selection' }).getByRole('button', { name: 'New Link' }).click();
    await settle(page);
    await shot(page, 'topology-new-link');
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: /Share/ }).first().click();
    await settle(page);
    await shot(page, 'topology-share');
  });

  test('topology with Projects', async ({ page, api }) => {
    // Projects on: alice's "Case 7" holds her #2 and #1 (ending tomorrow) with bob; bob holds #3
    // outside it, and #4 is in Quarantine in alice's "Bench"
    api.state.projects = true;
    for (const user of api.state.users) user.switchPassword = 'Lab-Pass1word';
    const now = new Date().toISOString();
    api.state.projectList.push(
      { id: 7, name: 'Case 7', reference: 'CS-00421337', owner: 1, archived_at: null, invite_token: 'tok7',
        members: [{ id: 1, joined_at: now, last_reserved_at: now }, { id: 2, joined_at: now }, { id: 3, joined_at: now }] },
      { id: 8, name: 'Bench', reference: '', owner: 1, archived_at: null, invite_token: 'tok8', members: [{ id: 1, joined_at: now }] },
    );
    api.state.reservations.find(r => r.switch === 2).project = 7;
    api.state.reservations.find(r => r.switch === 3).project = 99;
    api.state.reservations.push(
      { id: 50, switch: 1, user: 2, project: 7, creation_date: new Date(Date.now() - 6 * 24 * 60 * 60 * 1000).toISOString(),
        end_date: new Date(Date.now() + 26 * 60 * 60 * 1000).toISOString(), renewals_left: 2, admin_exception: false },
      { id: 51, switch: 4, user: 1, project: 8, creation_date: now,
        end_date: new Date(Date.now() + 5 * 24 * 60 * 60 * 1000).toISOString(), renewals_left: 2, admin_exception: false },
    );
    // Two Links between #2 and #1, and one from #2 out to #3
    for (const [a, b, svlan] of [[11, 22, 1002]]) {
      api.state.ports.find(p => p.id === a).svlan = svlan;
      api.state.ports.find(p => p.id === b).svlan = svlan;
    }
    api.state.ports.push({ id: 23, switch: 2, port_switch: '1/1/3', backbone: '10.69.144.1', port_backbone: '1/1/23', svlan: 1003, status: 'UP' },
      { id: 13, switch: 1, port_switch: '1/1/3', backbone: '10.69.144.1', port_backbone: '1/1/13', svlan: 1003, status: 'UP' },
      { id: 14, switch: 1, port_switch: '1/1/4', backbone: '10.69.144.1', port_backbone: '1/1/14', svlan: null, status: 'UP' });
    api.state.layouts.p7 = { 2: { x: 0, y: 0 }, 1: { x: 520, y: 0 }, 3: { x: 0, y: 300 } };
    await page.goto('/topology');
    await page.waitForTimeout(1500);
    await shot(page, 'topology-projects-picker');
    await page.getByRole('link', { name: /Case 7/ }).click();
    await waitForElement(page, 'switch_3');
    await page.waitForTimeout(1500);
    await shot(page, 'topology-project');
    await page.getByRole('button', { name: 'Legend' }).click();
    await clickElement(page, 'switch_2');
    await settle(page);
    await shot(page, 'topology-project-switch');
  });

  test('topology disconnect failed', async ({ page, api }) => {
    api.state.teardownErrors[1001] = "Ports failed to disconnect: 'no ethernet-service svlan 1001' failed on 10.69.144.1 (diag 2): ERROR: Invalid entity";
    await page.goto('/topology/user/1');
    await clickElement(page, 'link_1001');
    await settle(page);
    await shot(page, 'topology-disconnect-failed');
  });

  test('lab status', async ({ page, api }) => {
    await page.goto('/status');
    await settle(page);
    await shot(page, 'lab-status');
  });

  test('lab status with a Sweep report', async ({ page, api }) => {
    api.state.sweep = {
      started_at: new Date().toISOString(), finished_at: new Date().toISOString(),
      summary: 'Swept 4 Switch(es): Cleaned up 1, lifted 0 Quarantine(s), 3 problem(s).',
      problems: [
        '10.69.145.14 is in Quarantine (names carol): Unwanted cable on 1/1/5',
        '10.69.145.11 could not be Cleaned up: timed out',
        '10.69.145.13 has a local user BLab did not make: test',
      ],
    };
    await page.goto('/status');
    await settle(page);
    await shot(page, 'lab-status-sweep');
  });

  test('phone', async ({ page, api }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');
    await settle(page);
    await shot(page, 'phone-my-lab');
    await page.goto('/reservation');
    await settle(page);
    await shot(page, 'phone-reservation');
    await page.getByRole('button', { name: /Open main menu/ }).click();
    await settle(page);
    await page.screenshot({ path: `screenshots/${prefix}-phone-menu.png` });
  });
});
