// Snapshots and Restore (#45) on the mocked API (mock-api.js), with Projects on: Save with a note,
// Restore's confirm dialog (busy Switches left out with "free on", the AOS release warning), the
// progress per Switch, and members only.
import { test, expect } from './fixtures.js';

// alice's Project holding Switch 2 (her Reservation), with a Snapshot of Switches 1, 2 and 3
const setUp = (api) => {
  api.state.projects = true;
  for (const user of api.state.users) user.switchPassword = 'Lab-Pass1word';
  api.state.projectList.push({ id: 70, name: 'Upgrade tests', reference: '', owner: 1, archived_at: null, invite_token: 'tok70',
    members: [{ id: 1, joined_at: '2026-10-01T00:00:00Z' }] });
  api.state.reservations.find(r => r.switch === 2).project = 70;
  api.state.snapshots.push({ id: 7, project: 70, taken_at: '2026-10-10T09:00:00Z', taken_by: null, reason: 'release', note: '', links: 1,
    switches: [1, 2, 3].map(id => ({ id, model: api.state.switches[id - 1].model, mngt_IP: api.state.switches[id - 1].mngt_IP, aos_release: '8.10.86.R04',
      read_at: '2026-10-10T09:00:00Z', read_error: null, restorable: true })) });
};

test.beforeEach(({ api }) => setUp(api));

test("a member saves the Project with a note, from the Project's page", async ({ page, api }) => {
  await page.goto('/projects');
  await page.locator('[data-project="70"]').getByRole('link', { name: /Upgrade tests/ }).click();
  await page.getByRole('navigation', { name: 'Project sections' }).getByRole('link', { name: 'Snapshots' }).click();
  await expect(page).toHaveURL(/\/projects\/70\/snapshots$/);
  await expect(page.getByRole('heading', { name: 'Upgrade tests' })).toBeVisible();
  await expect(page.locator('[data-snapshot="7"]')).toContainText('Before Release');

  await page.getByLabel('Note (optional)').fill('VRRP working');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Saved.' })).toBeVisible();
  const saved = api.state.snapshots[0];
  expect(saved.note).toBe('VRRP working');
  await expect(page.locator(`[data-snapshot="${saved.id}"]`)).toContainText('VRRP working');
  await expect(page.locator(`[data-snapshot="${saved.id}"]`)).toContainText('Saved by alice');
  await expect(page.getByLabel('Note (optional)')).toHaveValue('');
});

test('Restore leaves busy Switches out with their free-on date, warns of another AOS release, and shows each Switch', async ({ page, api }) => {
  api.state.releases[2] = '8.10.99.R01';
  await page.goto('/projects/70/snapshots');
  await page.locator('[data-snapshot="7"]').getByRole('button', { name: 'Restore…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Restore this Snapshot?' });
  const plan = dialog.getByTestId('restore-plan');
  await expect(plan.locator('[data-switch="1"]')).toContainText('Free: reserved into the Project.');
  await expect(plan.locator('[data-switch="3"]')).toContainText('Reserved by someone else. Free on');
  await expect(plan.locator('[data-switch="3"] input')).toBeDisabled();
  await expect(plan.locator('[data-switch="2"]').getByTestId('release-warning'))
    .toContainText('the Snapshot was taken on 8.10.86.R04, the Switch runs 8.10.99.R01');
  await expect(plan.getByTestId('release-warning')).toHaveCount(1);

  await dialog.getByRole('button', { name: 'Restore 2 Switches' }).click();
  await expect(dialog).toBeHidden();
  expect(api.state.reservations.find(r => r.switch === 1).project).toBe(70);
  const progress = page.getByTestId('restore-progress');
  await expect(progress.getByRole('heading', { name: 'Restore under way' })).toBeVisible();
  await expect(progress.locator('[data-switch="1"]')).toContainText('Waiting');
  await expect(progress.locator('[data-switch="3"]')).toContainText('Left out');
  await expect(page.locator('[data-snapshot="7"]').getByRole('button', { name: 'Restore…' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();

  // The Switch worker gets on with it: the page follows
  const restore = api.state.restores[70];
  Object.assign(restore.switches[0], { state: 'done', detail: "Back, running the Snapshot's config." });
  Object.assign(restore.switches[1], { state: 'failed', detail: "Didn't come back within 20 minutes of its reload.",
    warnings: ['AOS release differs: the Snapshot was taken on 8.10.86.R04, the Switch runs 8.10.99.R01. Its config was loaded anyway.'] });
  restore.finished_at = new Date().toISOString();
  restore.links = [{ ends: [{ switch: 1, port: '1/1/1' }, { switch: 2, port: '1/1/1' }], outcome: 'built', detail: 'Built on SVLAN 1005.' }];
  await expect(progress.getByRole('heading', { name: 'Last Restore' })).toBeVisible({ timeout: 10000 });
  await expect(progress.locator('[data-switch="1"]')).toContainText('Done');
  await expect(progress.locator('[data-switch="2"]')).toContainText('Failed');
  await expect(progress.locator('[data-switch="2"]')).toContainText('AOS release differs');
  await expect(progress).toContainText('OS6860E-24 10.69.145.11 1/1/1 ↔ OS6900-X20 10.69.145.12 1/1/1: Built on SVLAN 1005.');
});

test('only for members', async ({ page, api }) => {
  api.state.projectList[0].members = [{ id: 2, joined_at: '2026-10-01T00:00:00Z' }];
  await page.goto('/projects/70/snapshots');
  await expect(page.getByTestId('no-such-project')).toBeVisible();
  expect(api.calls).not.toContain('POST projects/70/snapshots/');
});
