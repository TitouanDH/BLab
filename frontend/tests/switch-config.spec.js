// A Switch's Config (#44) on the mocked API (mock-api.js), with Projects on: the live config from
// the Project's Switches and My lab, the diff against the Golden init and between Snapshots, the
// rate-limited Read again, a Switch that can't be read, members and admins only.
import { test, expect } from './fixtures.js';

const RUNNING = 'system name "OS6900-X20"\nvlan 10 admin-state enable\n';

// alice's Project holding Switch 2 (her Reservation), with two Snapshots of Switches 1 and 2
const setUp = (api) => {
  api.state.projects = true;
  for (const user of api.state.users) user.switchPassword = 'Lab-Pass1word';
  api.state.projectList.push({ id: 70, name: 'Upgrade tests', reference: '', owner: 1, archived_at: null, invite_token: 'tok70',
    members: [{ id: 1, joined_at: '2026-10-01T00:00:00Z' }] });
  api.state.reservations.find(r => r.switch === 2).project = 70;
  const entry = (id) => ({ id, model: api.state.switches[id - 1].model, mngt_IP: api.state.switches[id - 1].mngt_IP,
    aos_release: '8.10.86.R04', read_at: '2026-10-10T09:00:00Z', read_error: null, restorable: true });
  api.state.snapshots.push(
    { id: 8, project: 70, taken_at: '2026-10-12T09:00:00Z', taken_by: 'alice', reason: 'save', note: 'VRRP working', links: 0, switches: [entry(1), entry(2)] },
    { id: 7, project: 70, taken_at: '2026-10-10T09:00:00Z', taken_by: null, reason: 'release', note: '', links: 0, switches: [entry(1), entry(2)] },
  );
  api.state.runningConfigs[2] = RUNNING;
  api.state.snapshotConfigs['7-2'] = 'system name "OS6900-X20"\nvlan 10 admin-state enable\n';
  api.state.snapshotConfigs['8-2'] = 'system name "OS6900-X20"\nvlan 10 admin-state enable\nvlan 20 admin-state enable\n';
};

test.beforeEach(({ api }) => setUp(api));

const diffRows = (page, op) => page.getByTestId('config-diff').locator(`[data-op="${op}"]`);

test("a member opens a Switch's live config from the Project and compares it with the Golden init", async ({ page, api }) => {
  await page.goto('/projects/70/switches');
  await page.locator('[data-switch="2"]').getByRole('link', { name: 'Config' }).click();
  await expect(page).toHaveURL(/\/switches\/2\/config$/);
  await expect(page.getByRole('heading', { name: 'OS6900-X20 10.69.145.12' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Switch sections' }).getByRole('link', { name: 'Config' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByLabel('Show')).toHaveValue('live');
  await expect(page.getByTestId('config-about')).toContainText('The running config (show configuration snapshot)');
  await expect(page.getByTestId('config-text')).toContainText('vlan 10 admin-state enable');
  // BLab reads it at most every 30 seconds
  await expect(page.getByTestId('read-again')).toBeDisabled();
  await expect(page.getByTestId('read-again')).toHaveText(/Read again in \d+ s/);

  await page.getByLabel('Compare with').selectOption('golden');
  await expect(page).toHaveURL(/against=golden/);
  await expect(page.getByTestId('diff-summary')).toContainText('1 line only here (+), 1 line only in Golden init (OS6900-X20) (-).');
  await expect(diffRows(page, 'added')).toHaveText(['+Added: vlan 10 admin-state enable']);
  await expect(diffRows(page, 'removed')).toHaveText(['-Removed: command-log enable']);

  await page.getByTestId('config-back').click();
  await expect(page).toHaveURL(/\/projects\/70\/switches$/);
});

test("a Snapshot's saved config, from the Snapshots, and the diff between two Snapshots", async ({ page, api }) => {
  await page.goto('/projects/70/snapshots');
  await page.locator('[data-snapshot="8"]').getByRole('link', { name: 'Config of OS6900-X20 10.69.145.12 in this Snapshot' }).click();
  await expect(page).toHaveURL(/\/switches\/2\/config\?show=snapshot-8$/);
  await expect(page.getByTestId('config-about')).toContainText('The saved config (working/vcboot.cfg)');
  await expect(page.getByTestId('config-text')).toContainText('vlan 20 admin-state enable');
  expect(api.state.liveReads[2]).toBeUndefined();  // a Snapshot needs no live read

  await page.getByLabel('Compare with').selectOption('snapshot-7');
  await expect(page.getByTestId('diff-summary')).toContainText('1 line only here (+), 0 lines only in Snapshot of');
  await expect(diffRows(page, 'added')).toHaveText(['+Added: vlan 20 admin-state enable']);
  await expect(diffRows(page, 'removed')).toHaveCount(0);
});

test('a long run of unchanged lines folds, all but the 3 lines next to a change', async ({ page, api }) => {
  const same = Array.from({ length: 20 }, (_, i) => `vlan ${100 + i} admin-state enable`).join('\n');
  api.state.snapshotConfigs['7-2'] = `${same}\nvlan 1 admin-state enable\n`;
  api.state.snapshotConfigs['8-2'] = `${same}\nvlan 2 admin-state enable\n`;
  await page.goto('/switches/2/config?show=snapshot-8&against=snapshot-7');
  const fold = page.getByRole('button', { name: 'Show 17 unchanged lines' });
  await expect(fold).toBeVisible();
  await expect(diffRows(page, 'same')).toHaveCount(3);  // the context before the change
  await fold.click();
  await expect(diffRows(page, 'same')).toHaveCount(20);
});

test("a Switch BLab can't read says why", async ({ page, api }) => {
  delete api.state.runningConfigs[2];
  await page.goto('/');
  await page.locator('[data-switch="2"]').getByRole('link', { name: 'Config' }).click();
  await expect(page.getByTestId('config-error')).toContainText("Live (running config) couldn't be read: Cannot connect to 10.69.145.12");
  await expect(page.getByTestId('config-text')).toHaveCount(0);
});

test('only for members and admins', async ({ page, api }) => {
  api.state.projectList[0].members = [{ id: 2, joined_at: '2026-10-01T00:00:00Z' }];
  api.state.reservations.find(r => r.switch === 2).user = 2;
  await page.goto('/switches/2/config');
  await expect(page.getByTestId('config-refused')).toContainText("Only the members of this Switch's Project, and admins, may see its config.");
  expect(api.state.liveReads[2]).toBeUndefined();
});

test("an admin reaches any Switch's config from Lab status", async ({ page, api }) => {
  api.state.staff = true;
  api.state.runningConfigs[3] = 'system name "OS6560-P24"\n';
  await page.addInitScript(() => localStorage.setItem('is_staff', 'true'));
  await page.goto('/status');
  await page.locator('[data-row="3"]').getByRole('link', { name: 'Config' }).click();
  await expect(page).toHaveURL(/\/switches\/3\/config$/);
  await expect(page.getByTestId('config-text')).toContainText('system name "OS6560-P24"');
  await expect(page.getByTestId('config-back')).toHaveText(/My lab/);  // not a member of its Project
});
