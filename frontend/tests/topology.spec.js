// The Topology on the mocked API (mock-api.js): the picker (Projects off), a Project's Topology on
// its page (Projects on), the device cards, Links from
// Switch to Switch with their ports at the ends, making a Link by dragging or through the New
// Link form, the legend pill, the side panel, Disconnect and Release, and a refresh that
// changes nothing on screen.
// Alice holds 10.69.145.12 (#2, ports 21 and 22); its port 21 has a Link (SVLAN 1001) to port
// 31 on bob's #3, which is outside her Topology. With Projects off, her Topology is
// /topology/user/1.
import { test, expect } from './fixtures.js';
import {
  clickElement, clickEmpty, dragSwitch, dragToConnect, elementData, hoverElement, pointOf, waitForElement, waitForGone,
} from './canvas.js';

const DAY = 24 * 60 * 60 * 1000;
const MINE = '/topology/user/1';
const panel = (page) => page.getByRole('complementary', { name: 'Selection' });
const popover = (page) => page.getByRole('dialog', { name: 'New Link' });
const legend = (page) => page.getByRole('region', { name: 'Legend' });

// Alice also holds 10.69.145.11 (#1, ports 11 and 12), so two free ports of hers can be connected
function aliceHoldsSwitch1(api) {
  api.state.reservations.push({ id: 50, switch: 1, user: 1, creation_date: new Date().toISOString(),
    end_date: new Date(Date.now() + 6 * DAY).toISOString(), renewals_left: 2, admin_exception: false });
}

// Projects on, every user with their Switch password, and alice's Project "Case 7" holding her #2
// with bob as a member; bob holds #3 outside it
function projectsOn(api) {
  api.state.projects = true;
  for (const user of api.state.users) user.switchPassword = 'Lab-Pass1word';
  const now = new Date().toISOString();
  api.state.projectList.push({ id: 7, name: 'Case 7', reference: 'CS-7', owner: 1, archived_at: null, invite_token: 'tok7',
    members: [{ id: 1, joined_at: now, last_reserved_at: now }, { id: 2, joined_at: now }] });
  api.state.projectList.push({ id: 8, name: 'Bench', reference: '', owner: 2, archived_at: null, invite_token: 'tok8',
    members: [{ id: 2, joined_at: now }, { id: 1, joined_at: now }] });
  api.state.reservations.find(r => r.switch === 2).project = 7;
  api.state.reservations.find(r => r.switch === 3).project = 99;  // someone else's Project
}

const viewport = (page) => page.evaluate(() => {
  const cy = window.__blabCanvas.cy;
  return { zoom: cy.zoom(), pan: cy.pan() };
});
const switchPosition = (page, switchId) => page.evaluate((id) => window.__blabCanvas.switchPositions()[id], switchId);
// What a card's image says, as text
const cardText = async (page, id) => decodeURIComponent((await elementData(page, id)).card).replace(/<[^>]+>/g, ' ');

test.describe('the picker', () => {
  test('with Projects off: my Topology and those shared with me, each opening its canvas', async ({ page, api }) => {
    await page.goto('/topology');
    const cards = page.getByRole('list', { name: 'Topologies' });
    await expect(cards.getByRole('link')).toHaveCount(2);
    await expect(cards.getByRole('link').nth(0)).toContainText('My Topology');
    await expect(cards.getByRole('link').nth(0)).toContainText('1 Switch');
    await expect(cards.getByRole('link').nth(1)).toContainText("bob's Topology");
    // A small preview of each
    await expect(cards.getByRole('img', { name: '1 Switch, 1 Link' }).first()).toBeVisible();

    await cards.getByRole('link', { name: /bob's Topology/ }).click();
    await expect(page).toHaveURL('/topology/user/2');
    await expect(page.getByRole('heading', { name: "bob's Topology" })).toBeVisible();
    await waitForElement(page, 'switch_3');

    // The back button, and the browser's
    await page.getByRole('link', { name: 'Back to the Topologies' }).click();
    await expect(page).toHaveURL('/topology');
    await page.goBack();
    await expect(page).toHaveURL('/topology/user/2');
    await waitForElement(page, 'switch_3');
  });

  test('with Projects on, the Topology is a Project\'s: the picker goes to the Projects', async ({ page, api }) => {
    projectsOn(api);
    await page.goto('/topology');
    await expect(page).toHaveURL('/projects');
    await expect(page.getByRole('list', { name: 'Projects' })).toBeVisible();
  });
});

test.describe('a Project\'s Topology, on its page', () => {
  test.beforeEach(({ api }) => projectsOn(api));

  test('opens on the canvas, without sharing', async ({ page, api }) => {
    await page.goto('/projects/7');
    await expect(page.getByRole('heading', { name: 'Case 7' })).toBeVisible();
    await expect(page.getByTestId('project-summary')).toHaveText('CS-7 · Owner: alice · 1 Switch · 2 members');
    await expect(page.getByRole('navigation', { name: 'Project sections' }).getByRole('link', { name: 'Topology' })).toHaveAttribute('aria-current', 'page');
    await waitForElement(page, 'switch_2');
    expect(api.calls).toContain('GET projects/7/topology/');
    await expect(page.getByRole('button', { name: 'Share Topology' })).toHaveCount(0);
  });

  test('a bookmarked /topology/<project> opens the Project\'s page', async ({ page, api }) => {
    await page.goto('/topology/7');
    await expect(page).toHaveURL('/projects/7');
    await waitForElement(page, 'switch_2');
    // Links from older pages still land there
    await page.goto('/topology?project=8');
    await expect(page).toHaveURL('/projects/8');
    await expect(page.getByRole('heading', { name: 'Bench' })).toBeVisible();
    await expect(page.getByTestId('empty-project')).toContainText('No Switch in this Project yet.');
  });

  test("a Project I'm not a member of says so", async ({ page, api }) => {
    await page.goto('/topology/99');
    await expect(page).toHaveURL('/projects/99');
    await expect(page.getByTestId('no-such-project')).toBeVisible();
    expect(api.calls).not.toContain('GET projects/99/topology/');
  });

  test('a Switch outside the Project shows without its IP', async ({ page, api }) => {
    await page.goto('/projects/7');
    await waitForElement(page, 'switch_3');
    expect(await cardText(page, 'switch_3')).toContain('IP for members only');
    expect(await cardText(page, 'switch_3')).toContain('OUTSIDE');
    expect(await cardText(page, 'switch_2')).toContain('10.69.145.12');
  });

  test('a free Tool port takes a New Link, and the Tool is drawn once a Link reaches it', async ({ page, api }) => {
    // The IXIA (#9) and a second Tool (#10), each with free ports; bob's Project already uses IXIA 1/1
    api.state.tools.push({ id: 9, model: 'Ixia XGS2', mngt_IP: 'Not available', console: '', part_number: '', hardware_revision: '', serial_number: 'X1' },
      { id: 10, model: 'GNS3', mngt_IP: 'Not available', console: '', part_number: '', hardware_revision: '', serial_number: 'G1' });
    api.state.ports.push(
      { id: 91, switch: 9, port_switch: '1/1', backbone: '10.69.144.1', port_backbone: '1/2/1', svlan: 1500, status: 'UP' },
      { id: 92, switch: 9, port_switch: '1/2', backbone: '10.69.144.1', port_backbone: '1/2/2', svlan: null, status: 'UP' },
      { id: 101, switch: 10, port_switch: '1/1', backbone: '10.69.144.1', port_backbone: '1/2/9', svlan: null, status: 'UP' },
    );
    await page.goto('/projects/7');
    await waitForElement(page, 'switch_2');
    expect(await page.evaluate(() => window.__blabCanvas.has('switch_9'))).toBe(false);

    await clickElement(page, 'switch_2');
    await panel(page).getByRole('button', { name: 'New Link' }).click();
    const form = panel(page).getByRole('form', { name: 'New Link' });
    // Bob's #3 outside the Project isn't offered; every Tool with a free port is
    await expect(form.getByLabel('To Switch').locator('option')).toHaveText(['OS6900-X20 #2', 'Ixia XGS2 #9', 'GNS3 #10']);
    // Never from a Tool to a Tool
    await form.getByLabel('From Switch').selectOption({ label: 'Ixia XGS2 #9' });
    await form.getByLabel('To Switch').selectOption({ label: 'GNS3 #10' });
    await expect(form.locator('[data-why-not]')).toHaveText('A Link joins a Tool to a Switch, not to another Tool.');
    await expect(form.getByRole('button', { name: 'Connect' })).toBeDisabled();

    await form.getByLabel('From Switch').selectOption({ label: 'OS6900-X20 #2' });
    await form.getByLabel('To Switch').selectOption({ label: 'Ixia XGS2 #9' });
    // Only the IXIA's free port: 1/1 is bob's
    await expect(form.getByLabel('To port').locator('option')).toHaveText(['1/2']);
    await form.getByRole('button', { name: 'Connect' }).click();
    await waitForElement(page, 'switch_9');
    expect(api.state.ports.filter(p => p.svlan === 1002).map(p => p.id).sort()).toEqual([22, 92]);
    expect(await elementData(page, 'switch_9')).toMatchObject({ state: 'tool', isTool: true });
    const card = await cardText(page, 'switch_9');
    expect(card).toContain('TOOL');
    expect(card).toContain('Shared by the lab');
    expect(card).not.toContain('IP for members only');

    // A Tool is never reserved: no Release, and the panel says how its Link ends
    await page.getByRole('button', { name: 'Fit to screen' }).click();
    await clickElement(page, 'switch_9');
    await expect(panel(page)).toContainText('Never reserved.');
    await expect(panel(page).getByRole('button', { name: 'Release' })).toHaveCount(0);
    await page.getByRole('button', { name: 'Legend' }).click();
    await expect(legend(page)).toContainText('A Tool: never reserved');
  });

  test('a user\'s Topology bookmarked before Projects were on goes to the Projects', async ({ page, api }) => {
    await page.goto(MINE);
    await expect(page).toHaveURL('/projects');
  });
});

test('device cards: model, id, IP, holder, time left and the state as a word', async ({ page, api }) => {
  // Alice's Reservation ends tomorrow
  api.state.reservations[0].end_date = new Date(Date.now() + DAY + 2 * 60 * 60 * 1000).toISOString();
  await page.goto(MINE);
  await waitForElement(page, 'switch_3');
  expect(await elementData(page, 'switch_2')).toMatchObject({ state: 'ending', holder: 'alice' });
  const mine = await cardText(page, 'switch_2');
  for (const text of ['OS6900-X20', '#2', '10.69.145.12', 'alice', '1d 1h left', 'ENDS SOON']) expect(mine).toContain(text);
  expect(await elementData(page, 'switch_3')).toMatchObject({ state: 'outside', holder: 'bob' });
  expect(await cardText(page, 'switch_3')).toContain('OUTSIDE');

  // Ports aren't drawn: the Link goes from card to card, with its port at each end
  expect(await page.evaluate(() => window.__blabCanvas.cy.nodes().length)).toBe(2);
  expect(await elementData(page, 'link_1001')).toMatchObject({
    source: 'switch_2', target: 'switch_3', sourceLabel: '1/1/1', targetLabel: '1/1/1', state: 'up',
  });

  // Link details on hover
  await hoverElement(page, 'link_1001');
  await expect(page.getByText('1/1/1 on OS6900-X20 #2 to 1/1/1 on OS6560-P24 #3, SVLAN 1001')).toBeVisible();
});

test('a Switch in Quarantine says so on its card', async ({ page, api }) => {
  api.state.quarantines[2] = { holder: 'alice', holder_id: 1, opened_at: new Date().toISOString(), reasons: ['Unwanted cable on 1/1/5'] };
  await page.goto(MINE);
  await expect.poll(async () => (await elementData(page, 'switch_2'))?.state).toBe('quarantine');
  expect(await cardText(page, 'switch_2')).toContain('QUARANTINE');
});

test('parallel Links between two Switches fan out', async ({ page, api }) => {
  aliceHoldsSwitch1(api);
  for (const [a, b, svlan] of [[11, 21, 1101], [12, 22, 1102]]) {
    api.state.ports.find(p => p.id === a).svlan = svlan;
    api.state.ports.find(p => p.id === b).svlan = svlan;
  }
  api.state.ports.find(p => p.id === 31).svlan = null;
  await page.goto(MINE);
  await waitForElement(page, 'link_1101');
  await waitForElement(page, 'link_1102');
  const [one, two] = [await pointOf(page, 'link_1101'), await pointOf(page, 'link_1102')];
  expect(Math.hypot(one.x - two.x, one.y - two.y)).toBeGreaterThan(20);
});

test('the side panel shows only while something is selected', async ({ page, api }) => {
  await page.goto(MINE);
  await waitForElement(page, 'switch_2');
  await expect(panel(page)).toHaveCount(0);

  await clickElement(page, 'switch_2');
  await expect(panel(page).getByRole('heading', { name: 'OS6900-X20 #2' })).toBeVisible();
  // Its Links, and the ports without one
  await expect(panel(page).getByTestId('switch-links')).toContainText('1/1/1 to 1/1/1 on OS6560-P24 #3');
  await expect(panel(page).getByTestId('unwired-ports')).toContainText('1/1/2: UNI 1/1/22 on backbone 10.69.144.1');
  await panel(page).getByRole('button', { name: /1\/1\/1 to/ }).click();
  await expect(panel(page).getByRole('heading', { name: 'Link' })).toBeVisible();

  await page.keyboard.press('Escape');
  await expect(panel(page)).toHaveCount(0);
  await clickElement(page, 'switch_3');
  await expect(panel(page)).toBeVisible();
  await clickEmpty(page);
  await expect(panel(page)).toHaveCount(0);
  await clickElement(page, 'switch_3');
  await panel(page).getByRole('button', { name: 'Close' }).click();
  await expect(panel(page)).toHaveCount(0);
});

test('the legend is a pill that lists only the states on the canvas', async ({ page, api }) => {
  api.state.ghosts[1001] = 'This Link is not carried by backbone 10.69.144.1: its Service there is missing.';
  await page.goto(MINE);
  await waitForElement(page, 'link_1001');
  await expect(legend(page)).toHaveCount(0);
  await page.getByRole('button', { name: 'Legend' }).click();
  await expect(legend(page)).toContainText('In this Topology');
  await expect(legend(page)).toContainText('Outside this Topology');
  await expect(legend(page)).toContainText('Carries no traffic');
  for (const absent of ['In Quarantine', 'Reservation ends in under 2 days', 'Being connected', 'Disconnect failed']) {
    await expect(legend(page)).not.toContainText(absent);
  }
  await page.getByRole('button', { name: 'Legend' }).click();
  await expect(legend(page)).toHaveCount(0);
});

test('drag from one Switch to another, pick the ports in the popover, Connect', async ({ page, api }) => {
  aliceHoldsSwitch1(api);
  await page.goto(MINE);
  await waitForElement(page, 'switch_1');
  await dragToConnect(page, 'switch_2', 'switch_1');

  // The first free port on each side is preselected
  await expect(popover(page)).toContainText('OS6900-X20 #2');
  await expect(popover(page)).toContainText('OS6860E-24 #1');
  await expect(popover(page).getByLabel('From port')).toHaveValue('22');
  await expect(popover(page).getByLabel('To port')).toHaveValue('11');
  await popover(page).getByLabel('To port').selectOption({ label: '1/1/2' });
  await popover(page).getByRole('button', { name: 'Connect' }).click();

  await waitForElement(page, 'link_1002');
  expect(await elementData(page, 'link_1002')).toMatchObject({ state: 'up', sourceLabel: '1/1/2', targetLabel: '1/1/2' });
  expect(api.state.ports.filter(p => p.svlan === 1002).map(p => p.id).sort()).toEqual([12, 22]);
  await expect(page.getByRole('status').filter({ hasText: /^Link made between/ })).toBeVisible();
  await expect(popover(page)).toHaveCount(0);
  // The new Link is selected
  await expect(panel(page).getByRole('heading', { name: 'Link' })).toBeVisible();
  await expect(panel(page)).toContainText('1002');
});

test('dropping on a Switch outside the Topology says why no Link can go there', async ({ page, api }) => {
  await page.goto(MINE);
  await waitForElement(page, 'switch_3');
  await dragToConnect(page, 'switch_2', 'switch_3');
  await expect(popover(page)).toContainText('OS6560-P24 #3 is outside this Topology: Links stay within it.');
  await expect(popover(page).getByRole('button', { name: 'Connect' })).toBeDisabled();
  await page.keyboard.press('Escape');
  await expect(popover(page)).toHaveCount(0);
  expect(api.calls).not.toContain('POST connect/');
});

test('the New Link form in the side panel connects the same way', async ({ page, api }) => {
  aliceHoldsSwitch1(api);
  await page.goto(MINE);
  await clickElement(page, 'switch_2');
  await panel(page).getByRole('button', { name: 'New Link' }).click();
  const form = panel(page).getByRole('form', { name: 'New Link' });
  await expect(form.getByLabel('From Switch')).toHaveValue('2');
  await expect(form.getByLabel('From port')).toHaveValue('22');
  // The other Switch of the Topology that has a free port; the one outside isn't offered
  await expect(form.getByLabel('To Switch')).toHaveValue('1');
  await expect(form.getByLabel('To Switch').locator('option')).toHaveText(['OS6860E-24 #1', 'OS6900-X20 #2']);
  await expect(form.getByLabel('To port')).toHaveValue('11');
  await form.getByRole('button', { name: 'Connect' }).click();
  await waitForElement(page, 'link_1002');
  expect(api.state.ports.filter(p => p.svlan === 1002).map(p => p.id).sort()).toEqual([11, 22]);

  // Also from the toolbar
  await page.getByRole('button', { name: 'New Link', exact: true }).first().click();
  await expect(panel(page).getByRole('form', { name: 'New Link' })).toBeVisible();
});

test('a refused Connect keeps the form open to try again', async ({ page, api }) => {
  aliceHoldsSwitch1(api);
  api.override('POST', 'connect/', () => ({ status: 400, body: { detail: 'No SVLAN left.' } }));
  await page.goto(MINE);
  await waitForElement(page, 'switch_1');
  await dragToConnect(page, 'switch_2', 'switch_1');
  await popover(page).getByRole('button', { name: 'Connect' }).click();
  await expect(page.getByRole('alert').filter({ hasText: "Couldn't connect the ports. No SVLAN left." })).toBeVisible();
  await expect(popover(page)).toBeVisible();
});

test('a look-only Topology offers no handle and no New Link', async ({ page, api }) => {
  api.override('GET', 'topology/1/', () => {
    const answer = api.handle('GET', 'topology/1/', null);
    return { ...answer, body: { ...answer.body, may_work: false } };
  });
  await page.goto(MINE);
  await waitForElement(page, 'switch_2');
  await hoverElement(page, 'switch_2');
  await expect(page.getByTestId('connect-handle')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'New Link' })).toHaveCount(0);
});

test('disconnect a Link: drawn as being disconnected until the Link worker is done', async ({ page, api }) => {
  await page.goto(MINE);
  await clickElement(page, 'link_1001');
  await expect(panel(page).getByRole('heading', { name: 'Link' })).toBeVisible();
  await expect(panel(page)).toContainText('1/1/1 on OS6900-X20 #2');
  await expect(panel(page)).toContainText('1/1/1 on OS6560-P24 #3');

  await panel(page).getByRole('button', { name: 'Disconnect' }).click();
  const dialog = page.getByRole('dialog', { name: 'Disconnect this Link?' });
  await expect(dialog).toContainText('SVLAN 1001');
  await dialog.getByRole('button', { name: 'Disconnect' }).click();

  await expect(page.getByRole('status').filter({ hasText: 'Disconnecting the Link.' })).toBeVisible();
  // The API hides it now, with its far end outside the Topology; the page keeps both drawn
  await page.waitForTimeout(2500);
  expect(await elementData(page, 'link_1001')).toMatchObject({ state: 'disconnecting' });
  expect(await elementData(page, 'switch_3')).not.toBeNull();
  await expect(panel(page).getByRole('button', { name: 'Disconnect' })).toBeDisabled();
  await expect(panel(page)).toContainText('This Link is being disconnected.');

  api.runLinkWorker();
  await waitForGone(page, 'link_1001');
  await waitForGone(page, 'switch_3');
  await expect(panel(page)).toHaveCount(0);
});

test('a failed disconnect is drawn as such, with the reason in plain words', async ({ page, api }) => {
  api.state.teardownErrors[1001] = "Ports failed to disconnect: 'no ethernet-service svlan 1001' failed on 10.69.144.1 (diag 2): ERROR: Invalid entity";
  await page.goto(MINE);
  await waitForElement(page, 'link_1001');
  expect(await elementData(page, 'link_1001')).toMatchObject({ state: 'failed' });
  await clickElement(page, 'link_1001');
  await expect(panel(page)).toContainText('Disconnecting this Link failed.');
  await expect(panel(page)).not.toContainText('diag');
  await expect(panel(page).getByRole('button', { name: 'Disconnect' })).toBeEnabled();
});

test('a Ghost Link is drawn in its own style, and the side panel says why it carries no traffic', async ({ page, api }) => {
  api.state.ghosts[1001] = 'This Link is not carried by backbone 10.69.144.1: its Service there is missing.';
  await page.goto(MINE);
  await waitForElement(page, 'link_1001');
  expect(await elementData(page, 'link_1001')).toMatchObject({ state: 'ghost' });
  const style = await page.evaluate(() => {
    const edge = window.__blabCanvas.cy.getElementById('link_1001');
    return { color: edge.style('line-color'), line: edge.style('line-style') };
  });
  expect(style).toEqual({ color: 'rgb(124,58,237)', line: 'dashed' });  // violet-600

  await clickElement(page, 'link_1001');
  await expect(panel(page)).toContainText('Carries no traffic');
  await expect(panel(page)).toContainText('This Link carries no traffic.');
  await expect(panel(page)).toContainText('This Link is not carried by backbone 10.69.144.1: its Service there is missing.');
  await expect(panel(page).getByRole('button', { name: 'Disconnect' })).toBeEnabled();

  // Carried again: the next refresh draws a plain Link
  delete api.state.ghosts[1001];
  await expect.poll(async () => (await elementData(page, 'link_1001')).state, { timeout: 15000 }).toBe('up');
  await expect(panel(page)).not.toContainText('This Link carries no traffic.');
});

test('a failed disconnect wins over a Ghost Link', async ({ page, api }) => {
  api.state.ghosts[1001] = 'This Link is not carried by backbone 10.69.144.1: its Service there is missing.';
  api.state.teardownErrors[1001] = 'Ports failed to disconnect: backbone unreachable';
  await page.goto(MINE);
  await waitForElement(page, 'link_1001');
  expect(await elementData(page, 'link_1001')).toMatchObject({ state: 'failed' });
});

test('the refresh keeps the selection and the view', async ({ page, api }) => {
  aliceHoldsSwitch1(api);
  await page.goto(MINE);
  await clickElement(page, 'switch_2');
  await page.getByRole('button', { name: 'Zoom in' }).click();
  const before = await viewport(page);
  const position = await switchPosition(page, 2);

  // Something changes elsewhere: bob's port gets a new Link
  api.state.ports.find(p => p.id === 32).svlan = 1500;
  await page.waitForTimeout(4500);  // two refreshes

  expect(await viewport(page)).toEqual(before);
  expect(await switchPosition(page, 2)).toEqual(position);
  await expect(panel(page).getByRole('heading', { name: 'OS6900-X20 #2' })).toBeVisible();
});

test('Release a Switch from the side panel', async ({ page, api }) => {
  await page.goto(MINE);
  await clickElement(page, 'switch_2');
  await expect(panel(page)).toContainText('10.69.145.12');
  await expect(panel(page)).toContainText('alice');
  await panel(page).getByRole('button', { name: 'Release' }).click();

  const dialog = page.getByRole('dialog', { name: 'Release this Switch?' });
  await dialog.getByRole('button', { name: 'Release' }).click();
  await expect(page.getByRole('status').filter({ hasText: /^Released\./ })).toBeVisible();
  await waitForGone(page, 'switch_2');
  await expect(page.getByText('No Switch in this Topology.')).toBeVisible();
});

test('a Switch outside the Topology cannot be Released from it', async ({ page, api }) => {
  await page.goto(MINE);
  await clickElement(page, 'switch_3');
  await expect(panel(page).getByRole('button', { name: 'Release' })).toBeDisabled();
  await expect(panel(page)).toContainText('This Switch is outside this Topology.');
});

test('a dragged Switch keeps its place on the server; Re-arrange lays everything out again', async ({ page, api }) => {
  await page.goto(MINE);
  await dragSwitch(page, 'switch_2', 80, 120);
  // Saved shortly after the drag ends, for every Switch drawn
  await expect.poll(() => api.calls.filter(c => c === 'PUT topology/1/layout/').length).toBe(1);
  const saved = api.state.layouts[1];
  expect(Object.keys(saved).sort()).toEqual(['2', '3']);
  expect(await page.evaluate(() => localStorage.getItem('topologyLayout.v2_1'))).toBeNull();

  await page.reload();
  await waitForElement(page, 'switch_2');
  const after = await switchPosition(page, 2);
  expect(after.x).toBeCloseTo(saved['2'].x, 0);
  expect(after.y).toBeCloseTo(saved['2'].y, 0);

  await page.getByRole('button', { name: 'Re-arrange' }).click();
  await expect.poll(() => api.calls).toContain('DELETE topology/1/layout/');
  expect(api.state.layouts[1]).toBeUndefined();
  const tidy = await switchPosition(page, 2);
  expect(tidy).not.toEqual(after);

  // Laid out tidily again after a reload too
  await page.reload();
  await waitForElement(page, 'switch_2');
  expect(await switchPosition(page, 2)).toEqual(tidy);
});

test('a Project layout is saved for the Project', async ({ page, api }) => {
  projectsOn(api);
  await page.goto('/projects/7');
  await dragSwitch(page, 'switch_2', 80, 120);
  await expect.poll(() => api.calls).toContain('PUT projects/7/layout/');
  expect(Object.keys(api.state.layouts.p7).sort()).toEqual(['2', '3']);
});

test('Re-arrange right after a drag is not undone by the save of the drag', async ({ page, api }) => {
  await page.goto(MINE);
  await dragSwitch(page, 'switch_2', 80, 120);
  await page.getByRole('button', { name: 'Re-arrange' }).click();
  await expect.poll(() => api.calls).toContain('DELETE topology/1/layout/');
  await page.waitForTimeout(800);  // longer than the save delay
  expect(api.state.layouts[1]).toBeUndefined();
});

test('a drag just before leaving the page is saved', async ({ page, api }) => {
  await page.goto(MINE);
  await dragSwitch(page, 'switch_2', 80, 120);
  await page.getByRole('link', { name: 'My lab' }).click();
  await expect.poll(() => api.state.layouts[1] && Object.keys(api.state.layouts[1]).sort()).toEqual(['2', '3']);
});

test('a shared Topology looks the same to everyone viewing it', async ({ page, api }) => {
  // Bob arranged his Topology, shared with alice
  api.state.layouts[2] = { 3: { x: 400, y: -150 } };
  await page.goto('/topology?owner=2');
  await expect(page).toHaveURL('/topology/user/2');
  await waitForElement(page, 'switch_3');
  expect(await switchPosition(page, 3)).toEqual({ x: 400, y: -150 });

  // Alice may work on it, so she may arrange it for both of them
  await dragSwitch(page, 'switch_3', 60, 40);
  await expect.poll(() => api.calls).toContain('PUT topology/2/layout/');
  expect(api.state.layouts[2][3]).not.toEqual({ x: 400, y: -150 });
});

test("this browser's own layout is uploaded once if the server has none, then dropped", async ({ page, api }) => {
  await page.addInitScript(() => {
    if (!sessionStorage.getItem('blab-test-layout')) {
      sessionStorage.setItem('blab-test-layout', '1');
      localStorage.setItem('topologyLayout.v2_1', JSON.stringify({ 2: { x: -300, y: 250 } }));
    }
  });
  await page.goto(MINE);
  await waitForElement(page, 'switch_2');
  expect(await switchPosition(page, 2)).toEqual({ x: -300, y: 250 });
  await expect.poll(() => api.state.layouts[1]).toEqual({ 2: { x: -300, y: 250 } });
  await expect.poll(() => page.evaluate(() => localStorage.getItem('topologyLayout.v2_1'))).toBeNull();

  await page.reload();
  await waitForElement(page, 'switch_2');
  expect(await switchPosition(page, 2)).toEqual({ x: -300, y: 250 });
  expect(api.calls.filter(c => c === 'PUT topology/1/layout/')).toHaveLength(1);
});

test('a layout already on the server wins over the one in this browser', async ({ page, api }) => {
  api.state.layouts[1] = { 2: { x: 10, y: 20 } };
  await page.addInitScript(() => localStorage.setItem('topologyLayout.v2_1', JSON.stringify({ 2: { x: -300, y: 250 } })));
  await page.goto(MINE);
  await waitForElement(page, 'switch_2');
  expect(await switchPosition(page, 2)).toEqual({ x: 10, y: 20 });
  expect(api.calls).not.toContain('PUT topology/1/layout/');
  expect(await page.evaluate(() => localStorage.getItem('topologyLayout.v2_1'))).toBeNull();
});

test('a Topology no longer shared falls back to my own', async ({ page, api }) => {
  api.state.shares = [];
  await page.goto('/topology/user/2');
  await expect(page.getByRole('alert')).toHaveText('This Topology is no longer shared with you.');
  await expect(page).toHaveURL(MINE);
  await expect(page.getByRole('heading', { name: 'My Topology' })).toBeVisible();
  await waitForElement(page, 'switch_2');
});

test('with Projects off, a Project address goes back to the picker', async ({ page, api }) => {
  for (const address of ['/topology/7', '/projects/7', '/projects']) {
    await page.goto(address);
    await expect(page).toHaveURL(/\/topology(\?project=7)?$/);
    await expect(page.getByRole('heading', { name: 'Topology', exact: true })).toBeVisible();
  }
  await expect(page.getByRole('alert')).toHaveCount(0);  // a plain redirect, not an error
  await expect(page.getByRole('navigation', { name: 'Global' }).getByRole('link', { name: 'Topology' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Global' }).getByRole('link', { name: 'Projects' })).toHaveCount(0);
});

test("a click on a card's handle opens the New Link form from that Switch", async ({ page, api }) => {
  aliceHoldsSwitch1(api);
  await page.goto(MINE);
  await hoverElement(page, 'switch_1');
  await page.getByTestId('connect-handle').click();
  const form = panel(page).getByRole('form', { name: 'New Link' });
  await expect(form.getByLabel('From Switch')).toHaveValue('1');
  await expect(form.getByLabel('To Switch')).toHaveValue('2');
});
