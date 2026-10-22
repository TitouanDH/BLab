// Contribute (#84: two cards), a cable (#58) on the mocked API (mock-api.js): alice holds Switch 2, bob Switch 3.
// The proposal is "1/1/24 -> 10.69.144.1 3/12", with other pairs to pick (#82), unless api.state.contribute says otherwise; a
// POST takes the cable in once api.state.contributionPlugged is set.
import { test, expect } from './fixtures.js';
import { clickElement, waitForElement } from './canvas.js';

const instruction = (page) => page.getByTestId('instruction');

test('Contribute opens on a card for each, and each flow has a way back', async ({ page, api }) => {
  await page.goto('/contribute');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Contribute');
  const cards = page.getByRole('link').filter({ has: page.getByRole('heading', { level: 2 }) });
  await expect(cards.getByRole('heading', { level: 2 })).toHaveText(['Contribute a cable', 'Contribute a Switch']);
  await expect(page.getByTestId('contribute-cable')).toContainText('You need:');

  await page.getByTestId('contribute-switch').click();
  await expect(page).toHaveURL(/\/contribute\?what=switch$/);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Contribute a Switch');
  await page.getByRole('link', { name: '← Contribute' }).click();
  await expect(page).toHaveURL(/\/contribute$/);

  await page.getByTestId('contribute-cable').click();
  await expect(page).toHaveURL(/\/contribute\?what=cable$/);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Contribute a cable');
  await page.goBack();
  await expect(page.getByTestId('contribute-switch')).toBeVisible();
});

test('the Switch card says how many of my Switch Contributions are in progress', async ({ page, api }) => {
  await page.goto('/contribute');
  await expect(page.getByTestId('contribute-switch')).toBeVisible();
  await expect(page.getByTestId('in-progress')).toHaveCount(0);
  await page.getByTestId('contribute-switch').click();
  await page.getByLabel('Model').fill('OS6860E-24');
  await page.getByRole('button', { name: 'Get its address and commands' }).click();
  await expect(page.getByTestId('contribution')).toHaveCount(1);
  await page.getByRole('link', { name: '← Contribute' }).click();
  await expect(page.getByTestId('contribute-switch').getByTestId('in-progress')).toHaveText('1 in progress');
  await expect(page.getByRole('link', { name: 'Contribute a Switch' })).toBeVisible();
});

test('an unknown flow shows the cards', async ({ page, api }) => {
  await page.goto('/contribute?what=nothing');
  await expect(page.getByTestId('contribute-cable')).toBeVisible();
});

test('it proposes a cable, says what is wrong, and takes it in once plugged', async ({ page, api }) => {
  await page.goto('/contribute?what=cable');
  const picker = page.getByLabel('Switch', { exact: true });
  // Free Switches and mine, never bob's
  await expect(picker.locator('option:not([disabled])')).toHaveText([
    'OS6860E-24 10.69.145.11 (2 Ports)', 'OS6900-X20 10.69.145.12 (2 Ports)', 'OS6465-P12 10.69.145.14 (0 Ports)',
    'OS6360-10 10.69.145.15 (0 Ports)',
  ]);
  await picker.selectOption({ label: 'OS6900-X20 10.69.145.12 (2 Ports)' });
  await expect(instruction(page)).toContainText('OS6900-X20 10.69.145.12 1/1/24');
  await expect(instruction(page)).toContainText('Backbone 10.69.144.1 3/12');
  await expect(page).toHaveURL(/\/contribute\?switch=2$/);

  await page.getByRole('button', { name: 'Plugged' }).click();
  await expect(page.getByTestId('check-failure')).toContainText('No link between 1/1/24 and port 3/12 of Backbone 10.69.144.1');

  api.state.contributionPlugged = true;
  await page.getByRole('button', { name: 'Plugged' }).click();
  await expect(page.getByTestId('check-done')).toContainText('1/1/24 is now a Port, to Backbone 10.69.144.1 port 3/12');
  await expect(picker.locator('option:checked')).toHaveText('OS6900-X20 10.69.145.12 (3 Ports)');
});

test('the user picks another Backbone, and a Switch with no free port says so', async ({ page, api }) => {
  await page.goto('/contribute?switch=1');
  await expect(instruction(page)).toContainText('Backbone 10.69.144.1 3/12');
  await page.getByLabel('Backbone', { exact: true }).selectOption('10.69.144.2');
  await expect(instruction(page)).toContainText('Backbone 10.69.144.2 3/40');

  api.state.contribute = { switch_port: null, problem: 'OS6860E-24 10.69.145.11 has no unused front port.' };
  await page.getByRole('button', { name: 'Propose again' }).click();
  await expect(page.getByTestId('proposal-problem')).toHaveText('OS6860E-24 10.69.145.11 has no unused front port.');
  await expect(instruction(page)).toHaveCount(0);
});

test('the user may plug into other ports that share a speed', async ({ page, api }) => {
  await page.goto('/contribute?switch=2');
  await expect(instruction(page)).toContainText('OS6900-X20 10.69.145.12 1/1/24');
  await expect(page.getByTestId('speeds')).toHaveText(/^10\/100\/1G ↔ and +10\/100\/1G$/);
  await page.getByText('Plug into other ports').click();
  const switchPort = page.getByLabel('Switch port');
  await expect(switchPort.locator('option')).toHaveText(['1/1/23 (10/100/1G)', '1/1/24 (10/100/1G)', '1/1/25 (1G/10G/25G)']);
  await switchPort.selectOption('1/1/25');
  // Its own Backbone ports, the same medium first: the cage
  const backbonePort = page.getByLabel('Port on Backbone 10.69.144.1');
  await expect(backbonePort.locator('option')).toHaveText(['1/2/9 (10G)', '3/12 (10/100/1G)', '3/13 (10/100/1G)']);
  await expect(instruction(page)).toContainText('1/1/25');
  await expect(instruction(page)).toContainText('Backbone 10.69.144.1 1/2/9');
  await backbonePort.selectOption('3/13');

  api.state.contributionPlugged = true;
  await page.getByRole('button', { name: 'Plugged' }).click();
  await expect(page.getByTestId('check-done')).toContainText('1/1/25 is now a Port, to Backbone 10.69.144.1 port 3/13');
});

test('a Quarantine for an Unwanted cable suggests contributing it', async ({ page, api }) => {
  Object.assign(api.state.quarantines[4], { holder: 'alice', holder_id: 1 });
  await page.goto('/');
  const nudge = page.getByTestId('quarantine-notice').getByTestId('contribute-nudge');
  await nudge.getByRole('link', { name: 'Contribute a cable' }).click();
  await expect(page).toHaveURL(/\/contribute\?switch=4$/);
  await expect(instruction(page)).toContainText('OS6465-P12 10.69.145.14 1/1/24');
});

test('a Switch with no free Port suggests contributing a cable on the Topology', async ({ page, api }) => {
  api.state.ports = api.state.ports.filter(p => !(p.switch === 2 && !p.svlan));
  await page.goto('/topology/user/1');
  await waitForElement(page, 'switch_2');
  await clickElement(page, 'switch_2');
  const panel = page.getByRole('complementary', { name: 'Selection' });
  await panel.getByTestId('contribute-nudge').getByRole('link', { name: 'Contribute a cable' }).click();
  await expect(page).toHaveURL(/\/contribute\?switch=2$/);
});

test('a proposal BLab refuses says why', async ({ page, api }) => {
  api.state.contributionRefused = "BLab couldn't read the ports of 10.69.145.11. Try again in a moment.";
  await page.goto('/contribute?switch=1');
  await expect(page.getByTestId('check-failure')).toContainText("BLab couldn't read the ports of 10.69.145.11");
  await expect(instruction(page)).toHaveCount(0);
});

test('a Switch: BLab gives its address and commands, and takes it in once Done', async ({ page, api }) => {
  await page.goto('/contribute');
  await page.getByTestId('contribute-switch').click();
  await expect(page).toHaveURL(/\/contribute\?what=switch$/);
  await page.getByLabel('Model').fill('OS6860E-24');
  await page.getByRole('button', { name: 'Get its address and commands' }).click();

  const contribution = page.getByTestId('contribution');
  await expect(contribution.getByRole('heading', { level: 2 })).toHaveText('OS6860E-24 10.69.144.150');
  await expect(contribution.getByTestId('commands').first()).toContainText('boot empipaddr 10.69.144.150');
  await expect(contribution.getByTestId('commands')).toHaveCount(3);

  await contribution.getByRole('button', { name: 'Done' }).click();
  await expect(contribution.getByTestId('contribution-failure')).toContainText('Nothing answers SSH at 10.69.144.150 yet');

  api.state.contributedSwitchAnswers = true;
  await contribution.getByRole('button', { name: 'Done' }).click();
  await expect(contribution.getByRole('status')).toContainText('BLab is taking it in');

  Object.assign(api.state.switchContributions[0], { state: 'done', switch: 1 });
  await page.reload();
  await contribution.getByRole('link', { name: 'Contribute a cable' }).click();
  await expect(page).toHaveURL(/\/contribute\?switch=1$/);
});

test('a Switch without an EMP is refused, and a Contribution can be cancelled', async ({ page, api }) => {
  await page.goto('/contribute?what=switch');
  await page.getByLabel('Model').fill('OS6360-10');
  await page.getByLabel('It has an EMP').uncheck();
  await page.getByRole('button', { name: 'Get its address and commands' }).click();
  await expect(page.getByRole('alert').or(page.getByRole('status')).filter({ hasText: 'without an EMP' }).first()).toBeVisible();
  await expect(page.getByTestId('contribution')).toHaveCount(0);

  await page.getByLabel('It has an EMP').check();
  await page.getByRole('button', { name: 'Get its address and commands' }).click();
  await page.getByTestId('contribution').getByRole('button', { name: 'Cancel' }).click();
  await expect(page.getByTestId('contribution')).toHaveCount(0);
});

test('a Contribution held too long says to start again', async ({ page, api }) => {
  api.state.switchContributions = [{ id: 90, model: 'OS6860E-24', mngt_IP: '10.69.144.150', has_emp: true, state: 'held',
    detail: '', created_at: new Date(Date.now() - 2 * 86400000).toISOString(),
    expires_at: new Date(Date.now() - 86400000).toISOString(), expired: true, switch: null, steps: [] }];
  await page.goto('/contribute?what=switch');
  await expect(page.getByTestId('contribution-expired')).toContainText('it is free again');
  await expect(page.getByTestId('contribution').getByRole('button', { name: 'Done' })).toHaveCount(0);
});
