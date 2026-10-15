// Projects (#41, #54) on the mocked API (mock-api.js), with BLAB_PROJECTS on: Reserve into a Project
// (last used preselected, "New Project…" inline, a first Reserve makes one), the Projects list
// and a Project's page (Topology, Switches, Snapshots, Members), a new Project opening on its
// empty Topology, the Invite link's confirm page (logged out: log in first, then back), the
// owner's management of members and the link, leaving, and archiving. With it off, none of it shows.
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
    await row(page, '1').getByRole('button', { name: /^More actions for/ }).click();
    await page.getByRole('menuitem', { name: /^Reserve until…/ }).click();
    const dialog = page.getByRole('dialog', { name: /Reserve OS6860E-24/ });
    await expect(dialog.getByLabel('Reserve into')).toHaveValue(String(created.id));
    await dialog.getByRole('button', { name: 'Reserve' }).click();
    await expect(dialog).toBeHidden();
    expect(api.state.reservations.find(r => r.switch === 1).project).toBe(created.id);
  });

  test('one Projects tab: a card per Project, opening its page', async ({ page, api }) => {
    bobsProject(api).members.push({ id: 1, joined_at: new Date().toISOString() });
    await page.goto('/');
    const nav = page.getByRole('navigation', { name: 'Global' });
    await nav.getByRole('link', { name: 'Projects' }).click();
    await expect(nav.getByRole('link', { name: 'Topology' })).toHaveCount(0);
    const card = page.locator('[data-project="50"]');
    await expect(card).toContainText('Case 42');
    await expect(card).toContainText('CS-42');
    await expect(card.getByTestId('project-counts')).toHaveText('1 Switch, 2 members');
    await expect(card.getByLabel('Members: bob, alice')).toBeVisible();
    await expect(card.getByRole('img', { name: '1 Switch, 1 Link' })).toBeVisible();

    await card.getByRole('link', { name: /Case 42/ }).click();
    await expect(page).toHaveURL('/projects/50');
    await expect(page.getByRole('heading', { name: 'Case 42' })).toBeVisible();
    await expect(nav.getByRole('link', { name: 'Projects' })).toHaveClass(/bg-primary-800/);  // still the tab shown
    const sections = page.getByRole('navigation', { name: 'Project sections' });
    await expect(sections.getByRole('link')).toHaveText(['Topology', 'Switches', 'Snapshots', 'Members']);

    // Each section at its own address, and back
    await sections.getByRole('link', { name: 'Switches' }).click();
    await expect(page).toHaveURL('/projects/50/switches');
    await expect(page.locator('[data-switch="3"]')).toContainText('Reserved by bob');
    await sections.getByRole('link', { name: 'Snapshots' }).click();
    await expect(page.getByTestId('no-snapshots')).toBeVisible();
    await sections.getByRole('link', { name: 'Members' }).click();
    await expect(page.locator('[data-member="bob"]')).toContainText('Owner');
    await page.getByRole('link', { name: 'My Projects' }).click();
    await expect(page).toHaveURL('/projects');
  });

  test('a new Project opens on its empty Topology, which leads to reserving into it', async ({ page, api }) => {
    await page.goto('/projects');
    await expect(page.getByTestId('no-projects')).toBeVisible();
    await page.getByRole('button', { name: 'New Project' }).click();
    await page.getByLabel('Name').fill('Bug 77');
    await page.getByRole('button', { name: 'Create' }).click();
    const created = api.state.projectList.find(p => p.name === 'Bug 77');
    await expect(page).toHaveURL(`/projects/${created.id}`);
    await expect(page.getByRole('heading', { name: 'Bug 77' })).toBeVisible();
    const empty = page.getByTestId('empty-project');
    await expect(empty).toContainText('No Switch in this Project yet.');

    await empty.getByRole('link', { name: 'Reserve a Switch into this Project' }).click();
    await expect(page).toHaveURL(`/reservation?project=${created.id}`);
    await expect(page.getByLabel('Reserve into').first().locator('option:checked')).toHaveText('Bug 77');
    await row(page, '1').getByRole('button', { name: 'Reserve', exact: true }).click();
    await expect(page.getByRole('status').filter({ hasText: 'in Bug 77' })).toBeVisible();
    expect(api.state.reservations.find(r => r.switch === 1).project).toBe(created.id);

    await page.goBack();
    await expect(page).toHaveURL(`/projects/${created.id}`);
    await expect(page.getByTestId('empty-project')).toBeHidden();
    await expect(page.getByTestId('project-summary')).toContainText('1 Switch');
  });

  test('Switches: any member Renews and Releases, and reserves more into the Project', async ({ page, api }) => {
    const p = bobsProject(api);
    p.members.push({ id: 1, joined_at: new Date().toISOString() });
    await page.goto('/projects/50/switches');
    const sw = page.locator('[data-switch="3"]');
    await expect(sw).toContainText('OS6560-P24 #3');
    await expect(sw).toContainText('1 Link on 2 cabled ports · 2 Renewals left');
    await sw.getByRole('button', { name: 'Renew' }).click();
    await expect(sw).toContainText('1 Renewal left');
    await expect(page.getByRole('link', { name: 'Reserve a Switch into this Project' })).toHaveAttribute('href', '/reservation?project=50');

    await sw.getByRole('button', { name: 'Release' }).click();
    await page.getByRole('dialog', { name: 'Release this Switch?' }).getByRole('button', { name: 'Release' }).click();
    await expect(page.getByTestId('no-switches')).toBeVisible();
    await expect(page.getByTestId('project-summary')).toContainText('0 Switches');
  });

  test('Members: the owner shares and renews the Invite link, and removes a member', async ({ page, api }) => {
    const p = api.state.projectList[api.state.projectList.push({ id: 60, name: 'Lab tests', reference: '', owner: 1, archived_at: null,
      invite_token: 'tok-old', members: [{ id: 1, joined_at: '2026-10-01T00:00:00Z' }, { id: 3, joined_at: '2026-10-02T00:00:00Z' }] }) - 1];
    await page.goto('/projects/60/members');
    await expect(page.getByLabel('Invite link')).toHaveValue(/\/join\/tok-old$/);
    await page.getByRole('button', { name: 'Renew link' }).click();
    await page.getByRole('dialog', { name: 'Renew the Invite link?' }).getByRole('button', { name: 'Renew' }).click();
    await expect(page.getByLabel('Invite link')).not.toHaveValue(/tok-old/);
    expect(p.invite_token).not.toBe('tok-old');

    await page.locator('[data-member="carol"]').getByRole('button', { name: 'Remove' }).click();
    await page.getByRole('dialog', { name: 'Remove carol?' }).getByRole('button', { name: 'Remove' }).click();
    await expect(page.locator('[data-member="carol"]')).toBeHidden();
    expect(p.members.map(m => m.id)).toEqual([1]);
    await expect(page.locator('[data-member="alice"]').getByRole('button', { name: 'Leave' })).toHaveCount(0);  // the owner archives instead
  });

  test('Archive stays on the list, once the Project holds no Switch', async ({ page, api }) => {
    const p = bobsProject(api);
    p.owner = 1;
    p.members.push({ id: 1, joined_at: new Date().toISOString() });
    await page.goto('/projects');
    const card = page.locator('[data-project="50"]');
    await expect(card.getByRole('button', { name: 'Archive' })).toBeDisabled();
    api.state.reservations = api.state.reservations.filter(r => r.project !== 50);
    await page.reload();
    await card.getByRole('button', { name: 'Archive' }).click();
    await page.getByRole('dialog', { name: 'Archive Case 42?' }).getByRole('button', { name: 'Archive' }).click();
    await expect(page.getByRole('region', { name: 'Archived' })).toContainText('Case 42');
    expect(p.invite_token).toBeNull();

    // Its page keeps its Snapshots, and offers no Reserve
    await page.getByRole('region', { name: 'Archived' }).getByRole('link', { name: /Case 42/ }).click();
    await expect(page).toHaveURL('/projects/50/snapshots');
    await page.getByRole('navigation', { name: 'Project sections' }).getByRole('link', { name: 'Topology' }).click();
    await expect(page.getByTestId('empty-project')).toHaveText('This Project is archived: it holds no Switch.');
    await expect(page.getByRole('link', { name: /Reserve a Switch/ })).toHaveCount(0);
    // Its members are only listed
    await page.getByRole('navigation', { name: 'Project sections' }).getByRole('link', { name: 'Members' }).click();
    await expect(page.locator('[data-member="bob"]')).toBeVisible();
    await expect(page.getByRole('list', { name: 'Members' }).getByRole('button')).toHaveCount(0);
  });

  test('a member who is not the owner may leave, from Members', async ({ page, api }) => {
    const p = bobsProject(api);
    p.members.push({ id: 1, joined_at: new Date().toISOString() });
    await page.goto('/projects');
    const card = page.locator('[data-project="50"]');
    await expect(card.getByRole('button', { name: 'Archive' })).toHaveCount(0);  // the owner's only
    await page.goto('/projects/50/members');
    await expect(page.getByTestId('invite-link')).toHaveCount(0);  // the owner's only
    await page.locator('[data-member="alice"]').getByRole('button', { name: 'Leave' }).click();
    await page.getByRole('dialog', { name: 'Leave Case 42?' }).getByRole('button', { name: 'Leave' }).click();
    await expect(page).toHaveURL('/projects');
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

    // A member's: the Project's page, on its Topology
    await page.getByRole('link', { name: 'Open the Project' }).click();
    await expect(page).toHaveURL('/projects/50');
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
