// The Topology canvas on the mocked API (mock-api.js): select, Connect, Disconnect, Release
// from the side panel, the states it draws, and a refresh that changes nothing on screen.
// Alice holds 10.69.145.12 (ports 21 and 22); its port 21 has a Link (SVLAN 1001) to port 31
// on bob's 10.69.145.13, which is outside her Topology.
import { test, expect } from './fixtures.js';
import { clickElement, elementData, hoverElement, waitForElement, waitForGone } from './canvas.js';

const DAY = 24 * 60 * 60 * 1000;
const panel = (page) => page.getByRole('complementary', { name: 'Selection' });
const banner = (page) => page.getByRole('status').filter({ hasText: /^Connecting/ });

// Alice also holds 10.69.145.11 (ports 11 and 12), so two free ports of hers can be connected
function aliceHoldsSwitch1(api) {
  api.state.reservations.push({ id: 50, switch: 1, user: 1, creation_date: new Date().toISOString(),
    end_date: new Date(Date.now() + 6 * DAY).toISOString(), renewals_left: 2, admin_exception: false });
}

const viewport = (page) => page.evaluate(() => {
  const cy = window.__blabCanvas.cy;
  return { zoom: cy.zoom(), pan: cy.pan() };
});

test('connect two ports: select a port, Connect, click another port, confirm', async ({ page, api }) => {
  aliceHoldsSwitch1(api);
  await page.goto('/topology');

  await clickElement(page, 'port_22');
  await expect(panel(page).getByRole('heading', { name: 'Port 1/1/2' })).toBeVisible();
  await expect(panel(page)).toContainText('No Link');
  await panel(page).getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(banner(page)).toContainText('Connecting 1/1/2 on OS6900-X20 (10.69.145.12): click another port, Esc to cancel');

  await clickElement(page, 'port_12');
  const dialog = page.getByRole('dialog', { name: 'Connect these ports?' });
  await expect(dialog).toContainText('1/1/2 on OS6900-X20 (10.69.145.12) and 1/1/2 on OS6860E-24 (10.69.145.11)');
  await dialog.getByRole('button', { name: 'Connect' }).click();

  await waitForElement(page, 'link_1002');
  const link = await elementData(page, 'link_1002');
  expect([link.source, link.target].sort()).toEqual(['port_12', 'port_22']);
  expect(link.state).toBe('up');
  await expect(page.getByRole('status').filter({ hasText: /^Link made between/ })).toBeVisible();
  await expect(banner(page)).toBeHidden();
  // The new Link is selected
  await expect(panel(page).getByRole('heading', { name: 'Link' })).toBeVisible();
  await expect(panel(page)).toContainText('1002');
  expect(api.calls).toContain('POST connect/');
});

test('disconnect a Link: drawn as being disconnected until the Link worker is done', async ({ page, api }) => {
  await page.goto('/topology');
  await clickElement(page, 'link_1001');
  await expect(panel(page).getByRole('heading', { name: 'Link' })).toBeVisible();
  await expect(panel(page)).toContainText('1/1/1 on OS6900-X20 (10.69.145.12)');
  await expect(panel(page)).toContainText('1/1/1 on OS6560-P24 (10.69.145.13)');

  await panel(page).getByRole('button', { name: 'Disconnect' }).click();
  const dialog = page.getByRole('dialog', { name: 'Disconnect this Link?' });
  await expect(dialog).toContainText('SVLAN 1001');
  await dialog.getByRole('button', { name: 'Disconnect' }).click();

  await expect(page.getByRole('status').filter({ hasText: 'Disconnecting the Link.' })).toBeVisible();
  // The API hides it now, with its far end outside the Topology; the page keeps both drawn
  await page.waitForTimeout(2500);
  expect(await elementData(page, 'link_1001')).toMatchObject({ state: 'disconnecting' });
  expect(await elementData(page, 'port_31')).not.toBeNull();
  await expect(panel(page).getByRole('button', { name: 'Disconnect' })).toBeDisabled();
  await expect(panel(page)).toContainText('This Link is being disconnected.');

  api.runLinkWorker();
  await waitForGone(page, 'link_1001');
  await waitForGone(page, 'switch_3');
  await expect(panel(page).getByRole('heading', { name: 'Nothing selected' })).toBeVisible();
});

test('a failed disconnect is drawn as such, with the reason in plain words', async ({ page, api }) => {
  api.state.teardownErrors[1001] = "Ports failed to disconnect: 'no ethernet-service svlan 1001' failed on 10.69.144.1 (diag 2): ERROR: Invalid entity";
  await page.goto('/topology');
  await waitForElement(page, 'link_1001');
  expect(await elementData(page, 'link_1001')).toMatchObject({ state: 'failed' });
  await clickElement(page, 'link_1001');
  await expect(panel(page)).toContainText('Disconnecting this Link failed.');
  await expect(panel(page)).not.toContainText('diag');
  await expect(panel(page).getByRole('button', { name: 'Disconnect' })).toBeEnabled();
});

test('a Ghost Link is drawn in its own style, and the side panel says why it carries no traffic', async ({ page, api }) => {
  api.state.ghosts[1001] = 'This Link is not carried by backbone 10.69.144.1: its Service there is missing.';
  await page.goto('/topology');
  await waitForElement(page, 'link_1001');
  expect(await elementData(page, 'link_1001')).toMatchObject({ state: 'ghost' });
  const style = await page.evaluate(() => {
    const edge = window.__blabCanvas.cy.getElementById('link_1001');
    return { color: edge.style('line-color'), line: edge.style('line-style') };
  });
  expect(style).toEqual({ color: 'rgb(124,58,237)', line: 'dashed' });  // violet-600
  await expect(page.getByRole('region', { name: 'Legend' })).toContainText('Carries no traffic');

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
  await page.goto('/topology');
  await waitForElement(page, 'link_1001');
  expect(await elementData(page, 'link_1001')).toMatchObject({ state: 'failed' });
});

test('Esc leaves connect mode; ports that cannot be connected say why', async ({ page, api }) => {
  await page.goto('/topology');
  await clickElement(page, 'port_22');
  await panel(page).getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(banner(page)).toBeVisible();

  // Outside the Topology: refused in the banner, still connecting
  await clickElement(page, 'port_31');
  await expect(banner(page)).toContainText('This port is on a Switch outside this Topology.');
  await expect(page.getByRole('dialog')).toHaveCount(0);

  await page.keyboard.press('Escape');
  await expect(banner(page)).toBeHidden();

  // A port with a Link: Connect is disabled, and says why
  await clickElement(page, 'port_21');
  await expect(panel(page).getByRole('button', { name: 'Connect', exact: true })).toHaveCount(0);
  await expect(panel(page)).toContainText('To 1/1/1 on OS6560-P24 (10.69.145.13), SVLAN 1001');
  await clickElement(page, 'port_31');
  await expect(panel(page).getByRole('button', { name: 'Show Link' })).toBeVisible();
});

test('the refresh keeps the selection, the connect mode and the view', async ({ page, api }) => {
  aliceHoldsSwitch1(api);
  await page.goto('/topology');
  await clickElement(page, 'port_22');
  await panel(page).getByRole('button', { name: 'Connect', exact: true }).click();
  await page.getByRole('button', { name: 'Zoom in' }).click();
  const before = await viewport(page);
  const position = await page.evaluate(() => window.__blabCanvas.cy.getElementById('port_22').position());

  // Something changes elsewhere: bob's port gets a new Link
  api.state.ports.find(p => p.id === 32).svlan = 1500;
  await page.waitForTimeout(4500);  // two refreshes

  expect(await viewport(page)).toEqual(before);
  expect(await page.evaluate(() => window.__blabCanvas.cy.getElementById('port_22').position())).toEqual(position);
  await expect(banner(page)).toBeVisible();
  await expect(panel(page).getByRole('heading', { name: 'Port 1/1/2' })).toBeVisible();
});

test('Release a Switch from the side panel', async ({ page, api }) => {
  await page.goto('/topology');
  await clickElement(page, 'switch_2');
  await expect(panel(page).getByRole('heading', { name: 'OS6900-X20' })).toBeVisible();
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
  await page.goto('/topology');
  await clickElement(page, 'switch_3');
  await expect(panel(page).getByRole('button', { name: 'Release' })).toBeDisabled();
  await expect(panel(page)).toContainText('This Switch is outside this Topology.');
});

test('states read from the canvas and the legend', async ({ page, api }) => {
  // Alice's Reservation ends tomorrow
  api.state.reservations[0].end_date = new Date(Date.now() + DAY).toISOString();
  await page.goto('/topology');
  await waitForElement(page, 'switch_3');
  expect(await elementData(page, 'switch_2')).toMatchObject({ state: 'ending', holder: 'alice' });
  expect(await elementData(page, 'switch_3')).toMatchObject({ state: 'outside', holder: 'bob' });
  expect(await elementData(page, 'port_21')).toMatchObject({ state: 'linked' });
  expect(await elementData(page, 'port_22')).toMatchObject({ state: 'free' });

  const legend = page.getByRole('region', { name: 'Legend' });
  for (const label of ['Switch in this Topology', 'Switch outside this Topology', 'Reservation ends in under 2 days',
    'In Quarantine', 'Port with a Link', 'Being connected', 'Being disconnected', 'Disconnect failed']) {
    await expect(legend).toContainText(label);
  }

  // Link details on hover
  await hoverElement(page, 'link_1001');
  await expect(page.getByText('1/1/1 on OS6900-X20 (10.69.145.12) to 1/1/1 on OS6560-P24 (10.69.145.13), SVLAN 1001')).toBeVisible();
});

async function dragSwitch(page, id, dx, dy) {
  const box = await page.evaluate((id) => {
    const cy = window.__blabCanvas.cy;
    const bb = cy.getElementById(id).renderedBoundingBox({ includeLabels: false });
    const c = cy.container().getBoundingClientRect();
    // Its bottom edge: a Link may arch over its top
    return { x: c.left + (bb.x1 + bb.x2) / 2, y: c.top + bb.y2 - 4 };
  }, id);
  await page.mouse.move(box.x, box.y);
  await page.mouse.down();
  await page.mouse.move(box.x + dx, box.y + dy, { steps: 8 });
  await page.mouse.up();
}

const switchPosition = (page, switchId) => page.evaluate((id) => window.__blabCanvas.switchPositions()[id], switchId);

test('a dragged Switch keeps its place on the server; Re-arrange lays everything out again', async ({ page, api }) => {
  await page.goto('/topology');
  await waitForElement(page, 'switch_2');
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

test('Re-arrange right after a drag is not undone by the save of the drag', async ({ page, api }) => {
  await page.goto('/topology');
  await waitForElement(page, 'switch_2');
  await dragSwitch(page, 'switch_2', 80, 120);
  await page.getByRole('button', { name: 'Re-arrange' }).click();
  await expect.poll(() => api.calls).toContain('DELETE topology/1/layout/');
  await page.waitForTimeout(800);  // longer than the save delay
  expect(api.state.layouts[1]).toBeUndefined();
});

test('a drag just before leaving the page is saved', async ({ page, api }) => {
  await page.goto('/topology');
  await waitForElement(page, 'switch_2');
  await dragSwitch(page, 'switch_2', 80, 120);
  await page.getByRole('link', { name: 'My lab' }).click();
  await expect.poll(() => api.state.layouts[1] && Object.keys(api.state.layouts[1]).sort()).toEqual(['2', '3']);
});

test('a shared Topology looks the same to everyone viewing it', async ({ page, api }) => {
  // Bob arranged his Topology, shared with alice
  api.state.layouts[2] = { 3: { x: 400, y: -150 } };
  await page.goto('/topology?owner=2');
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
  await page.goto('/topology');
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
  await page.goto('/topology');
  await waitForElement(page, 'switch_2');
  expect(await switchPosition(page, 2)).toEqual({ x: 10, y: 20 });
  expect(api.calls).not.toContain('PUT topology/1/layout/');
  expect(await page.evaluate(() => localStorage.getItem('topologyLayout.v2_1'))).toBeNull();
});

test('cancelling the Connect confirmation goes back to choosing the other port', async ({ page, api }) => {
  aliceHoldsSwitch1(api);
  await page.goto('/topology');
  await clickElement(page, 'port_22');
  await panel(page).getByRole('button', { name: 'Connect', exact: true }).click();
  await clickElement(page, 'port_12');
  await page.getByRole('dialog', { name: 'Connect these ports?' }).getByRole('button', { name: 'Cancel' }).click();
  await expect(banner(page)).toBeVisible();
  await clickElement(page, 'port_11');
  await expect(page.getByRole('dialog', { name: 'Connect these ports?' })).toContainText('1/1/1 on OS6860E-24 (10.69.145.11)');
  expect(api.calls).not.toContain('POST connect/');
});
