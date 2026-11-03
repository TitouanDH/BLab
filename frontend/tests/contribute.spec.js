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
  const picker = page.getByRole('combobox', { name: 'Switch', exact: true });
  await picker.click();
  // Free Switches and mine, never bob's, each with its model, IP and Ports
  const options = page.getByRole('option');
  await expect(options).toHaveText([
    /OS6860E-24\s*10\.69\.145\.11\s*2 Ports/, /OS6900-X20\s*10\.69\.145\.12\s*2 Ports/,
    /OS6465-P12\s*10\.69\.145\.14\s*0 Ports/, /OS6360-10\s*10\.69\.145\.15\s*0 Ports/,
  ]);
  await options.filter({ hasText: '10.69.145.12' }).click();
  await expect(picker).toHaveValue('OS6900-X20 10.69.145.12');
  await expect(instruction(page)).toContainText('OS6900-X20 10.69.145.12 1/1/24');
  await expect(instruction(page)).toContainText('Backbone 10.69.144.1 3/12');
  await expect(page.getByTestId('plugged-hint')).toContainText('BLab enables port 3/12 of Backbone 10.69.144.1');
  await expect(page).toHaveURL(/\/contribute\?switch=2$/);

  await page.getByRole('button', { name: 'Plugged' }).click();
  await expect(page.getByTestId('check-failure')).toContainText('No link between 1/1/24 and port 3/12 of Backbone 10.69.144.1');

  api.state.contributionPlugged = true;
  await page.getByRole('button', { name: 'Plugged' }).click();
  const done = page.getByTestId('check-done');
  await expect(done).toContainText('Cable recorded');
  await expect(done).toContainText('port 1/1/24 of OS6900-X20 10.69.145.12 is now a Port, cabled to UNI 3/12 of Backbone 10.69.144.1');
  await expect(done).toContainText('Leave the cable in place');
  await picker.click();
  await expect(options.filter({ hasText: '10.69.145.12' })).toContainText('3 Ports');
  await page.keyboard.press('Escape');

  // Another cable to the same Switch: BLab proposes again
  await done.getByRole('button', { name: 'Add another cable to this Switch' }).click();
  await expect(instruction(page)).toContainText('OS6900-X20 10.69.145.12 1/1/24');
  await expect(done).toHaveCount(0);
});

test('the Switch picker filters by model or IP as the user types, and works from the keyboard', async ({ page, api }) => {
  await page.goto('/contribute?what=cable');
  const picker = page.getByRole('combobox', { name: 'Switch', exact: true });
  const options = page.getByRole('option');
  await picker.fill('6465');
  await expect(options).toHaveText([/OS6465-P12\s*10\.69\.145\.14/]);
  // Every word must match, in any order, the IP in part
  await picker.fill('145.1 os6');
  await expect(options).toHaveCount(4);
  await picker.fill('145.15');
  await expect(options).toHaveText([/OS6360-10\s*10\.69\.145\.15/]);
  await picker.fill('9999');
  await expect(options).toHaveCount(0);
  await expect(page.getByTestId('no-switch-match')).toHaveText('No Switch matches “9999”.');

  // Arrows move, Enter picks
  await picker.fill('10.69.145');
  await picker.press('ArrowDown');
  await expect(page.locator('[role="option"][data-headlessui-state~="active"]')).toContainText('10.69.145.12');
  await picker.press('Enter');
  await expect(picker).toHaveValue('OS6900-X20 10.69.145.12');
  await expect(picker).toHaveAttribute('aria-expanded', 'false');
  await expect(instruction(page)).toContainText('OS6900-X20 10.69.145.12 1/1/24');
  await expect(page).toHaveURL(/\/contribute\?switch=2$/);

  // Escape gives up the search and keeps the Switch picked
  await picker.fill('6465');
  await picker.press('Escape');
  await expect(picker).toHaveValue('OS6900-X20 10.69.145.12');
  await expect(page).toHaveURL(/\/contribute\?switch=2$/);
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
  await expect(page.getByTestId('check-done')).toContainText('port 1/1/25 of OS6900-X20 10.69.145.12 is now a Port, cabled to UNI 3/13 of Backbone 10.69.144.1');
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
  await expect(contribution.getByTestId('commands').nth(1)).toContainText('boot empipaddr 10.69.144.150');
  await expect(contribution.getByTestId('commands')).toHaveCount(8);

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

test.describe('the EMP commands come one line a block, each pasted on its own (#89)', () => {
  test.use({ permissions: ['clipboard-read', 'clipboard-write'] });

  test('a block ends at each question, with the answer after it, and Copy copies exactly its line', async ({ page, api }) => {
    await page.goto('/contribute?what=switch');
    await page.getByLabel('Model').fill('OS6860E-24');
    await page.getByRole('button', { name: 'Get its address and commands' }).click();

    const emp = page.getByTestId('contribution').getByRole('listitem').first();
    await expect(emp.getByTestId('commands')).toHaveText([
      'modify boot parameters', 'boot empipaddr 10.69.144.150', 'boot empmasklength 26', 'commit boot', 'commit system', 'exit',
    ]);
    await expect(emp.getByTestId('then')).toHaveText([
      'Then: Wait for the Boot > prompt.', 'Then: It asks whether to commit to NVRAM anyway: answer y.',
    ]);
    // Each answer sits right after the block that asks it
    const asking = emp.locator('[data-testid="command-block"]:has(+ [data-testid="then"]) [data-testid="commands"]');
    await expect(asking).toHaveText(['modify boot parameters', 'commit boot']);

    // No line break for Windows to turn into CR LF, and nothing before or after the command
    await emp.getByRole('button', { name: 'Copy block 2' }).click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe('boot empipaddr 10.69.144.150');
    await emp.getByRole('button', { name: 'Copy block 4' }).click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe('commit boot');
  });
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
