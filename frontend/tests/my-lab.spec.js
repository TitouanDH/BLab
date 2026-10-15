// My lab, the page after logging in (#31, #42), on the mocked API (mock-api.js): Alice holds
// Switch 2 (2 Renewals left), linked by SVLAN 1001 to Switch 3; Bob holds Switch 3 and shares
// his Topology with her. With Projects on, every Switch I may work on is grouped by Project.
import { test, expect } from './fixtures.js';

const GOOD = 'Lab-Pass1word';
const row = (page, id) => page.locator(`[data-switch="${id}"]`);
const group = (page, name) => page.getByRole('region', { name });

test('My Reservations: Renew pushes the end date back and counts down', async ({ page, api }) => {
  await page.goto('/');
  const mine = row(page, '2');
  await expect(mine).toContainText('OS6900-X20');
  await expect(mine).toContainText('(in 4 days)');
  await expect(mine).toContainText('2 Renewals left');
  await expect(mine.getByRole('progressbar', { name: 'Time left' })).toBeVisible();
  await mine.getByRole('button', { name: 'Renew' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Renewed for another 7 days. Renewals left: 1.' })).toBeVisible();
  await expect(mine).toContainText('1 Renewal left');
  await expect(mine).toContainText('(in 1 week)');
  expect(api.calls).toContain('POST renew/');
});

test('a Reservation that cannot be Renewed says why instead', async ({ page, api }) => {
  api.state.reservations[0].renewals_left = 0;
  await page.goto('/');
  const mine = row(page, '2');
  await expect(mine).toContainText('No Renewals left: it has been Renewed 2 times.');
  await expect(mine.getByRole('button', { name: /Renew/ })).toHaveCount(0);
});

test('a refused Renewal shows the server reason', async ({ page, api }) => {
  api.override('POST', 'renew/', () => ({ status: 400, body: { detail: 'This Reservation has expired: it is being released.' } }));
  await page.goto('/');
  await row(page, '2').getByRole('button', { name: /Renew/ }).click();
  await expect(page.getByRole('alert')).toHaveText("Couldn't Renew this Reservation. This Reservation has expired: it is being released.");
});

test('Release from My lab', async ({ page, api }) => {
  await page.goto('/');
  await row(page, '2').getByRole('button', { name: 'Release' }).click();
  await page.getByRole('dialog', { name: 'Release this Switch?' }).getByRole('button', { name: 'Release' }).click();
  await expect(page.getByRole('status').filter({ hasText: /^Released\./ })).toBeVisible();
  await expect(page.getByTestId('empty-state')).toBeVisible();
  await expect(row(page, '3')).toBeVisible();  // bob's stays
});

test('a Quarantine naming me says what was found, that Reservations are blocked, and how to clear it', async ({ page, api }) => {
  Object.assign(api.state.quarantines[4], { holder: 'alice', holder_id: 1 });
  await page.goto('/');
  const notice = page.getByTestId('quarantine-notice');
  await expect(notice.getByRole('heading')).toHaveText('A Quarantine names you');
  await expect(notice).toContainText("you can't make new Reservations");
  await expect(row(page, '4')).toContainText('Unwanted cable on 1/1/5');
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
  await expect(row(page, '2')).toBeVisible();
  await expect(page.getByTestId('quarantine-notice')).toHaveCount(0);
});

test.describe('with Projects off', () => {
  test('my Reservations, then each Topology shared with me, each opening its Topology', async ({ page, api }) => {
    await page.goto('/');
    const groups = page.getByTestId('lab-group');
    await expect(groups.getByRole('heading', { level: 2 })).toHaveText(['My Reservations', "bob's Topology"]);
    await expect(group(page, 'My Reservations').locator('[data-switch]')).toHaveCount(1);
    await expect(group(page, "bob's Topology").locator('[data-switch="3"]')).toContainText('Held by bob');
    await expect(row(page, '2')).not.toContainText('Held by');
    await expect(page.getByTestId('topology-miniature')).toHaveCount(0);  // the miniature is gone

    await group(page, "bob's Topology").getByRole('link', { name: /Open Topology/ }).click();
    await expect(page).toHaveURL('/topology/user/2');
    await expect(page.getByRole('heading', { name: "bob's Topology" })).toBeVisible();
    await page.goBack();
    await group(page, 'My Reservations').getByRole('link', { name: /Open Topology/ }).click();
    await expect(page).toHaveURL('/topology/user/1');
  });

  test('a Topology shared with me that holds nothing still shows', async ({ page, api }) => {
    api.state.reservations = api.state.reservations.filter(r => r.user !== 2);
    await page.goto('/');
    await expect(group(page, "bob's Topology")).toContainText('bob holds no Switch now.');
  });

  test('with nothing reserved, it points to Reservation', async ({ page, api }) => {
    api.state.reservations = api.state.reservations.filter(r => r.user !== 1);
    await page.goto('/');
    const empty = page.getByTestId('empty-state');
    await expect(empty).toContainText('You hold no Reservation.');
    await empty.getByRole('link', { name: 'Reserve a Switch' }).click();
    await expect(page).toHaveURL('/reservation');
  });
});

test.describe('my login on each Switch', () => {
  test.use({ permissions: ['clipboard-read', 'clipboard-write'] });

  test('Copy SSH command gives ssh <login>@<ip>', async ({ page, api }) => {
    await page.goto('/');
    const login = row(page, '2').locator('[data-login]');
    await expect(login).toContainText('alice');
    await login.getByRole('button', { name: 'Copy SSH command' }).click();
    await expect(page.getByRole('status').filter({ hasText: 'SSH command copied.' })).toBeVisible();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('ssh alice@10.69.145.12');
    // On bob's Switch too, with my own login
    await row(page, '3').getByRole('button', { name: 'Copy SSH command' }).click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe('ssh alice@10.69.145.13');
  });

  test('before the Switch password, BLab\'s password is shown only on demand', async ({ page, api }) => {
    await page.goto('/');
    const login = row(page, '2').locator('[data-login]');
    await expect(login.locator('[data-password]')).not.toContainText('Pw-alice-2x');
    await login.getByRole('button', { name: 'Show' }).click();
    await expect(login.locator('[data-password]')).toHaveText('Pw-alice-2x');
  });

  test('a Switch account being created, or that BLab could not create yet, says so', async ({ page, api }) => {
    api.state.accountsPending = [2];
    api.state.accountErrors[3] = 'Cannot connect to 10.69.145.13: timed out';
    await page.goto('/');
    await expect(row(page, '2').locator('[data-login]')).toHaveText('Your Switch account is being created...');
    await expect(row(page, '2').getByRole('button', { name: 'Copy SSH command' })).toHaveCount(0);
    await expect(row(page, '3').locator('[data-login]')).toContainText('BLab keeps trying: Cannot connect to 10.69.145.13: timed out');
    // Once created, the next poll shows it
    api.state.accountsPending = [];
    await expect(row(page, '2').getByRole('button', { name: 'Copy SSH command' })).toBeVisible({ timeout: 8000 });
  });
});

test('a Switch with a Ghost Link has a dot, and counts its Links', async ({ page, api }) => {
  await page.goto('/');
  await expect(row(page, '2').locator('[data-links]')).toHaveText('1 Link');
  await expect(row(page, '2').locator('[data-ghost-dot]')).toHaveCount(0);

  api.state.ghosts[1001] = 'This Link is not carried by backbone 10.69.144.1: its Service there is missing.';
  await page.reload();
  for (const id of ['2', '3']) {  // both ends
    await expect(row(page, id).locator('[data-ghost-dot]')).toHaveAttribute('title', /1 Ghost Link: a Link of this Switch carries no traffic/);
    await expect(row(page, id).locator('[data-links]')).toContainText('1 Ghost Link');
  }
});

test.describe('with Projects on', () => {
  // alice's "Case 7" (bob a member) holds her #2 and bob's #1; her "Bench" holds nothing; bob's
  // #3 is in his own Project, which alice is not in
  test.beforeEach(({ api }) => {
    api.state.projects = true;
    for (const user of api.state.users) user.switchPassword = GOOD;
    const now = new Date().toISOString();
    api.state.projectList.push(
      { id: 7, name: 'Case 7', reference: 'CS-00421337', owner: 1, archived_at: null, invite_token: 'tok7',
        members: [{ id: 1, joined_at: now }, { id: 2, joined_at: now }] },
      { id: 8, name: 'Bench', reference: '', owner: 1, archived_at: null, invite_token: 'tok8', members: [{ id: 1, joined_at: now }] },
      { id: 9, name: "bob's lab", reference: '', owner: 2, archived_at: null, invite_token: 'tok9', members: [{ id: 2, joined_at: now }] },
    );
    api.state.reservations.find(r => r.switch === 2).project = 7;
    api.state.reservations.find(r => r.switch === 3).project = 9;
    api.state.reservations.push({ id: 50, switch: 1, user: 2, project: 7, creation_date: now,
      end_date: new Date(Date.now() + 26 * 60 * 60 * 1000).toISOString(), renewals_left: 2, admin_exception: false });
  });

  test('one section per Project: name, members, Invite link, Open Topology, and its Switches', async ({ page, api }) => {
    await page.goto('/');
    const groups = page.getByTestId('lab-group');
    await expect(groups.getByRole('heading', { level: 2 })).toHaveText(['Bench', /^Case 7/]);
    const caseSeven = group(page, /Case 7/);
    await expect(caseSeven.getByRole('heading')).toContainText('CS-00421337');
    await expect(caseSeven.locator('[data-members]')).toHaveText('2 members: alice (owner), bob');
    await expect(caseSeven.locator('[data-switch]')).toHaveCount(2);
    await expect(row(page, '1')).toContainText('Held by bob');
    await expect(row(page, '1').locator('[data-login]')).toContainText('alice');  // my login on bob's Switch
    await expect(row(page, '3')).toHaveCount(0);  // bob's Project is not mine
    await expect(group(page, 'Bench')).toContainText('No Switch is reserved into Bench.');
    await expect(page.locator('#switch-accounts')).toContainText('your Switch password');
    await expect(page.locator('[data-password]')).toHaveCount(0);
    await expect(page.getByText(GOOD)).toHaveCount(0);

    await caseSeven.getByRole('link', { name: /Open Topology/ }).click();
    await expect(page).toHaveURL('/projects/7');  // the Project page, which opens on its Topology
    await page.goBack();
    await group(page, 'Bench').getByRole('link', { name: 'Reserve a Switch into this Project' }).click();
    await expect(page).toHaveURL('/reservation?project=8');
  });

  test.describe('copying', () => {
    test.use({ permissions: ['clipboard-read', 'clipboard-write'] });

    test('the owner copies the Invite link', async ({ page, api }) => {
      await page.goto('/');
      await group(page, /Case 7/).getByRole('button', { name: 'Copy Invite link' }).click();
      await expect(page.getByRole('status').filter({ hasText: 'Invite link copied.' })).toBeVisible();
      expect(await page.evaluate(() => navigator.clipboard.readText())).toMatch(/\/join\/tok7$/);
    });
  });

  test('a member who is not the owner has no Invite link to copy', async ({ page, api }) => {
    api.state.projectList.find(p => p.id === 7).owner = 2;
    await page.goto('/');
    await expect(group(page, /Case 7/).locator('[data-members]')).toHaveText('2 members: alice, bob (owner)');
    await expect(group(page, /Case 7/).getByRole('button', { name: 'Copy Invite link' })).toHaveCount(0);
  });

  test('in no Project yet, it points to Reservation', async ({ page, api }) => {
    api.state.projectList = api.state.projectList.filter(p => p.id === 9);
    api.state.reservations = api.state.reservations.filter(r => r.switch === 3);
    await page.goto('/');
    await expect(page.getByTestId('empty-state')).toContainText('You are in no Project yet.');
    await expect(page.getByTestId('lab-group')).toHaveCount(0);
  });
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
  await expect(page).toHaveURL('/topology/user/1');
  await expect(page.getByRole('heading', { name: 'My Topology' })).toBeVisible();
  await page.waitForTimeout(2500);
  expect(api.calls.filter(c => c === 'GET topology/2/').length).toBe(1);
});
