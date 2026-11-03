// A member's AOS install (#49) on the mocked API (mock-api.js), with Projects on: the release
// select of the Switch's family, the install asked for, its progress on the dialog and the row,
// a failed one, and a refused one.
import { test, expect } from './fixtures.js';

// alice's Project holding Switch 2 (her Reservation)
const setUp = (api) => {
  api.state.projects = true;
  for (const user of api.state.users) user.switchPassword = 'Lab-Pass1word';
  api.state.projectList.push({ id: 70, name: 'Upgrade tests', reference: '', owner: 1, archived_at: null, invite_token: 'tok70',
    members: [{ id: 1, joined_at: '2026-10-01T00:00:00Z' }] });
  api.state.reservations.find(r => r.switch === 2).project = 70;
  api.state.releases[2] = '8.10.86.R04';
};

test.beforeEach(({ api }) => setUp(api));

const openDialog = async (page) => {
  await page.goto('/projects/70/switches');
  await page.locator('[data-switch="2"]').getByRole('button', { name: 'Change AOS…' }).click();
  return page.getByRole('dialog', { name: /^AOS release of/ });
};

test('a member picks another release of the family, and follows its install', async ({ page, api }) => {
  const dialog = await openDialog(page);
  await expect(dialog.getByTestId('aos-current')).toHaveText('8.10.86.R04');
  const select = dialog.getByLabel('AOS release');
  await expect(select.locator('option')).toHaveText(["8.11.162.R01 (the lab's)", '8.10.86.R04 (running)', '8.10.105.R02']);
  await expect(select).toHaveValue('8.11.162.R01');  // the lab's, which it doesn't run
  await expect(dialog).toContainText('save your config first');
  await select.selectOption('8.10.105.R02');
  await dialog.getByRole('button', { name: 'Install and reboot' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'BLab installs AOS 8.10.105.R02' })).toBeVisible();
  expect(api.state.aosInstalls[2].release).toBe('8.10.105.R02');
  const progress = dialog.getByTestId('aos-install');
  await expect(progress).toHaveAttribute('data-state', 'waiting');
  await expect(dialog.getByRole('button', { name: 'Install and reboot' })).toBeDisabled();  // one at a time

  // The Switch worker moves it on: the dialog follows, and so does the Switch's row
  Object.assign(api.state.aosInstalls[2], { state: 'reloading', detail: 'Reloading into AOS 8.10.105.R02.' });
  await expect(progress).toHaveAttribute('data-state', 'reloading');
  await expect(progress).toContainText('AOS 8.10.105.R02: rebooting');
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  await expect(page.locator('[data-switch="2"]').getByTestId('aos-install-progress'))
    .toContainText('AOS 8.10.105.R02: Reloading into AOS 8.10.105.R02.');

  Object.assign(api.state.aosInstalls[2], { state: 'done', detail: 'Back, running AOS 8.10.105.R02.' });
  api.state.releases[2] = '8.10.105.R02';
  await expect(page.locator('[data-switch="2"]').getByTestId('aos-install-progress'))
    .toContainText('AOS 8.10.105.R02: Back, running AOS 8.10.105.R02.');
  await expect(page.locator('[data-switch="2"]')).toContainText('AOS 8.10.105.R02');
});

test('a failed install says why, and another may be asked for', async ({ page, api }) => {
  api.state.aosInstalls[2] = { id: 900, switch: 2, release: '8.11.162.R01', previous_release: '8.10.86.R04', requested_by: 'alice',
    requested_at: '2026-10-21T09:00:00Z', state: 'failed', warnings: [], updated_at: '2026-10-21T09:05:00Z', finished_at: '2026-10-21T09:05:00Z',
    detail: "Couldn't install AOS 8.11.162.R01: cannot reach the image server: timed out. The Switch still runs 8.10.86.R04." };
  const dialog = await openDialog(page);
  await expect(dialog.getByTestId('aos-install')).toHaveAttribute('data-state', 'failed');
  await expect(dialog.getByTestId('aos-install')).toContainText('The Switch still runs 8.10.86.R04.');
  await expect(dialog.getByRole('button', { name: 'Install and reboot' })).toBeEnabled();
});

test('a refused install shows the API\'s reason', async ({ page, api }) => {
  api.override('POST', 'switches/2/aos_release/', () => ({ status: 400, body: { detail: 'This Switch is being Restored: install a release once that is done.' } }));
  const dialog = await openDialog(page);
  await dialog.getByRole('button', { name: 'Install and reboot' }).click();
  await expect(page.getByRole('alert')).toContainText('This Switch is being Restored');
  expect(api.state.aosInstalls[2]).toBeUndefined();
});
