// Projects (#41) on the mocked API (mock-api.js), with BLAB_PROJECTS on: Reserve into a Project
// (last used preselected, "New Project…" inline, a first Reserve makes one), the Invite link's
// confirm page (logged out: log in first, then back), the owner's management of members and the
// link, leaving, archiving, and the Topology of a Project. With it off, none of it shows.
import { test, expect } from './fixtures.js';

const GOOD = 'Lab-Pass1word';
const row = (page, id) => page.locator(`[data-switch="${id}"]`);

// Projects on, and every user has chosen their Switch password
const projectsOn = (api) => {
  api.state.projects = true;
  for (const user of api.state.users) user.switchPassword = GOOD;
};

// bob's Project with Switch 3 in it, alice not a member
const bobsProject = (api) => {
  const p = { id: 50, name: 'Case 42', reference: 'CS-42', owner: 2, archived_at: null, invite_token: 'tok-bob',
    members: [{ id: 2, joined_at: new Date().toISOString() }] };
  api.state.projectList.push(p);
  api.state.reservations.find(r => r.switch === 3).project = 50;
  return p;
};

test.describe('with Projects on', () => {
  test.beforeEach(({ api }) => projectsOn(api));

  test("a first Reserve makes the user's Project, which is preselected next time", async ({ page, api }) => {
    await page.goto('/reservation');
    const choice = page.getByLabel('Reserve into').first();
    await expect(choice).toHaveValue('');
    await expect(choice.locator('option:checked')).toHaveText('A new Project for you');
    await row(page, '1').getByRole('button', { name: 'Reserve', exact: true }).click();
    await expect(page.getByRole('status').filter({ hasText: "in alice's lab" })).toBeVisible();
    const project = api.state.projectList.find(p => p.name === "alice's lab");
    expect(api.state.reservations.find(r => r.switch === 1).project).toBe(project.id);
    await expect(choice.locator('option:checked')).toHaveText("alice's lab");
    await expect(page.getByRole('link', { name: 'Projects' })).toBeVisible();  // the navbar knows now
  });

  test('"New Project…" names one inline, and Reserve until… offers the same choice', async ({ page, api }) => {
    await page.goto('/reservation');
    await page.getByLabel('Reserve into').first().selectOption('new');
    await row(page, '1').getByRole('button', { name: 'Reserve', exact: true }).click();
    await expect(page.getByRole('alert').filter({ hasText: 'Name the new Project first.' })).toBeVisible();
    expect(api.calls).not.toContain('POST reserve/');

    await page.getByLabel('New Project name').first().fill('Bug 1234');
    await page.getByLabel('New Project reference').first().fill('CS-1234');
    await row(page, '1').getByRole('button', { name: 'Reserve', exact: true }).click();
    await expect(page.getByRole('status').filter({ hasText: 'in Bug 1234' })).toBeVisible();
    const created = api.state.projectList.find(p => p.name === 'Bug 1234');
    expect(created.reference).toBe('CS-1234');

    api.state.reservations = api.state.reservations.filter(r => r.switch !== 1);
    await row(page, '1').getByRole('button', { name: 'Reserve until…' }).click();
    const dialog = page.getByRole('dialog', { name: /Reserve OS6860E-24/ });
    await expect(dialog.getByLabel('Reserve into')).toHaveValue(String(created.id));
    await dialog.getByRole('button', { name: 'Reserve' }).click();
    await expect(dialog).toBeHidden();
    expect(api.state.reservations.find(r => r.switch === 1).project).toBe(created.id);
  });

  test('the owner shares and renews the Invite link, and removes a member', async ({ page, api }) => {
    const p = api.state.projectList[api.state.projectList.push({ id: 60, name: 'Lab tests', reference: '', owner: 1, archived_at: null,
      invite_token: 'tok-old', members: [{ id: 1, joined_at: '2026-10-01T00:00:00Z' }, { id: 3, joined_at: '2026-10-02T00:00:00Z' }] }) - 1];
    await page.goto('/projects');
    const card = page.locator('[data-project="60"]');
    await expect(card.getByLabel('Invite link')).toHaveValue(/\/join\/tok-old$/);
    await card.getByRole('button', { name: 'Renew link' }).click();
    await page.getByRole('dialog', { name: 'Renew the Invite link?' }).getByRole('button', { name: 'Renew' }).click();
    await expect(card.getByLabel('Invite link')).not.toHaveValue(/tok-old/);
    expect(p.invite_token).not.toBe('tok-old');

    await card.locator('[data-member="carol"]').getByRole('button', { name: 'Remove' }).click();
    await page.getByRole('dialog', { name: 'Remove carol?' }).getByRole('button', { name: 'Remove' }).click();
    await expect(card.locator('[data-member="carol"]')).toBeHidden();
    expect(p.members.map(m => m.id)).toEqual([1]);

    // Empty: it can be archived, and it moves under Archived
    await card.getByRole('button', { name: 'Archive' }).click();
    await page.getByRole('dialog', { name: 'Archive Lab tests?' }).getByRole('button', { name: 'Archive' }).click();
    await expect(page.getByRole('region', { name: 'Archived' })).toContainText('Lab tests');
    expect(p.invite_token).toBeNull();
  });

  test('a Project that holds Switches cannot be archived; a member may leave', async ({ page, api }) => {
    const p = bobsProject(api);
    p.members.push({ id: 1, joined_at: new Date().toISOString() });
    await page.goto('/projects');
    const card = page.locator('[data-project="50"]');
    await expect(card.getByTestId('invite-link')).toHaveCount(0);  // the owner's only
    await expect(card.getByRole('button', { name: 'Archive' })).toHaveCount(0);
    await card.getByRole('button', { name: 'Leave' }).click();
    await page.getByRole('dialog', { name: 'Leave Case 42?' }).getByRole('button', { name: 'Leave' }).click();
    await expect(card).toBeHidden();
    expect(p.members.map(m => m.id)).toEqual([2]);
  });

  test('the Invite link shows the Project, then Join makes the user a member', async ({ page, api }) => {
    const p = bobsProject(api);
    await page.goto('/join/tok-bob');
    await expect(page.getByRole('heading', { name: 'Case 42' })).toBeVisible();
    await expect(page.getByTestId('join-owner')).toHaveText('bob');
    await expect(page.getByTestId('join-switches')).toHaveText('OS6560-P24 #3');
    await page.getByRole('button', { name: 'Join Case 42' }).click();
    await expect(page.getByRole('status').filter({ hasText: 'You joined Case 42.' })).toBeVisible();
    await expect(page.getByText('You are a member of this Project.')).toBeVisible();
    expect(p.members.map(m => m.id)).toContain(1);

    // A member's Topology: the Project's
    await page.getByRole('link', { name: 'Open its Topology' }).click();
    await expect(page).toHaveURL('/topology/50');
    await expect.poll(() => api.calls).toContain('GET projects/50/topology/');
    await expect(page.getByRole('heading', { name: 'Case 42' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Share Topology' })).toHaveCount(0);
  });

  test('a renewed or archived Invite link says it no longer works', async ({ page }) => {
    await page.goto('/join/nothing');
    await expect(page.getByTestId('join-problem')).toContainText('may have been renewed');
  });
});

test.describe('logged out, with Projects on', () => {
  test.use({ loggedInAs: null });
  test.beforeEach(({ api }) => projectsOn(api));

  test('the Invite link asks to log in first, then comes back to its confirm page', async ({ page, api }) => {
    bobsProject(api);
    await page.goto('/join/tok-bob');
    await expect(page).toHaveURL('/login?redirect=/join/tok-bob');
    await page.getByLabel('Username').fill('alice');
    await page.getByLabel('Password').fill('secret');
    await page.getByRole('button', { name: 'Log in' }).click();
    await expect(page).toHaveURL('/join/tok-bob');
    await expect(page.getByRole('button', { name: 'Join Case 42' })).toBeVisible();
  });
});

test('with Projects off, nothing of them shows', async ({ page, api }) => {
  await page.goto('/reservation');
  await expect(row(page, '1')).toBeVisible();
  await expect(page.getByTestId('project-choice')).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Projects' })).toHaveCount(0);
  await row(page, '1').getByRole('button', { name: 'Reserve', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Reservation successful.' })).toBeVisible();
  expect(api.state.reservations.find(r => r.switch === 1).project).toBeUndefined();
});
