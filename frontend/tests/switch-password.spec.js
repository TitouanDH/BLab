// The Switch password (#40): with Projects on, every user chooses one after their email,
// before any other page, and can change it on the Account page. It is never shown again.
import { test, expect } from './fixtures.js';

const GOOD = 'Lab-Pass1word';

const choose = async (page, password, again = password) => {
  await page.getByLabel('New Switch password').fill(password);
  await page.getByLabel('Type it again').fill(again);
};
const saveButton = (page) => page.getByRole('button', { name: 'Save Switch password' });

test.describe('with Projects on', () => {
  test.beforeEach(({ api }) => { api.state.projects = true; });

  test('every page waits for the Switch password, then My lab opens', async ({ page, api }) => {
    for (const path of ['/', '/reservation', '/topology', '/status']) {
      await page.goto(path);
      await expect(page).toHaveURL('/account');
    }
    await expect(page.getByText('Choose your Switch password before you go on')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Choose your Switch password' })).toBeVisible();
    await expect(page.getByText('Don\'t reuse a password from elsewhere.', { exact: false })).toBeVisible();

    await choose(page, GOOD);
    await saveButton(page).click();
    await expect(page).toHaveURL('/');
    await expect(page.getByRole('heading', { name: 'My lab' })).toBeVisible();
    expect(api.state.me.switchPassword).toBe(GOOD);
  });

  test('the rules are checked as the user types', async ({ page }) => {
    await page.goto('/account');
    const rule = (id) => page.locator(`[data-rule="${id}"]`);
    await page.getByLabel('New Switch password').fill('alice');
    await expect(rule('length')).toHaveAttribute('data-passed', 'false');
    await expect(rule('username')).toHaveAttribute('data-passed', 'false');
    await expect(rule('lower')).toHaveAttribute('data-passed', 'true');
    await expect(saveButton(page)).toBeDisabled();

    await choose(page, 'Switch@123');
    await expect(rule('old')).toHaveAttribute('data-passed', 'false');
    await expect(saveButton(page)).toBeDisabled();

    await choose(page, 'Lab-Pass1word!');
    await expect(rule('chars')).toHaveAttribute('data-passed', 'false');

    await choose(page, GOOD, 'Lab-Pass1wore');
    await expect(page.getByText('The two don\'t match.')).toBeVisible();
    await expect(saveButton(page)).toBeDisabled();

    await choose(page, GOOD);
    for (const id of ['length', 'upper', 'lower', 'digit', 'symbol', 'chars', 'username', 'old']) {
      await expect(rule(id)).toHaveAttribute('data-passed', 'true');
    }
    await expect(saveButton(page)).toBeEnabled();
  });

  test('a password BLab refuses says why, and the page stays', async ({ page, api }) => {
    api.override('POST', 'switch_password/', () => ({ status: 400, body: { password: ['It must not contain your username.'] } }));
    await page.goto('/account');
    await choose(page, GOOD);
    await saveButton(page).click();
    await expect(page.getByRole('alert').filter({ hasText: 'It must not contain your username.' })).toBeVisible();
    await expect(page).toHaveURL('/account');
  });

  test('without an email, the email comes first, then the Switch password', async ({ page, api }) => {
    api.state.me.email = '';
    await page.goto('/');
    await expect(page).toHaveURL('/account');
    await expect(page.getByLabel('New Switch password')).toHaveCount(0);
    await page.getByLabel('Email').fill('alice@example.com');
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(page).toHaveURL('/account');
    await choose(page, GOOD);
    await saveButton(page).click();
    await expect(page).toHaveURL('/');
  });

  test('the Switch accounts show the login and "Your Switch password", never a password', async ({ page, api }) => {
    api.state.me.switchPassword = GOOD;
    await page.goto('/#switch-accounts');
    const accounts = page.getByRole('region', { name: 'Your Switch accounts' });
    await expect(accounts.locator('[data-switch-password]').first()).toHaveText('Your Switch password');
    await expect(accounts.getByRole('button', { name: 'Show' })).toHaveCount(0);
    await expect(accounts).not.toContainText(GOOD);
  });

  test('it can be changed later from the Account page, which stays open', async ({ page, api }) => {
    api.state.me.switchPassword = GOOD;
    await page.goto('/account');
    await expect(page.getByRole('heading', { name: 'Change your Switch password' })).toBeVisible();
    await expect(page.getByLabel('New Switch password')).toHaveValue('');
    await choose(page, 'Other-Pass2');
    await saveButton(page).click();
    await expect(page.getByRole('status').filter({ hasText: 'Switch password saved.' })).toBeVisible();
    await expect(page).toHaveURL('/account');
    expect(api.state.me.switchPassword).toBe('Other-Pass2');
  });

  test('a Switch BLab could not reach yet is named', async ({ page, api }) => {
    api.state.me.switchPassword = GOOD;
    api.state.accountErrors[2] = 'unreachable';
    await page.goto('/account');
    await choose(page, 'Other-Pass2');
    await saveButton(page).click();
    await expect(page.getByRole('alert').filter({ hasText: "BLab couldn't set it yet on 10.69.145.12" })).toBeVisible();
  });

  test('when BLab refuses for a missing Switch password, the page goes to the Account page', async ({ page, api }) => {
    api.state.me.switchPassword = GOOD;
    await page.goto('/reservation');
    await expect(page.locator('[data-switch]').first()).toBeVisible();
    api.state.me.switchPassword = undefined;  // say an admin removed it meanwhile
    await page.reload();  // this browser remembers it is done: the page loads, BLab refuses it
    await expect(page).toHaveURL('/account');
    await expect(page.getByRole('heading', { name: 'Choose your Switch password' })).toBeVisible();
  });
});

test('with Projects off, the Account page has no Switch password and nothing waits for it', async ({ page, api }) => {
  await page.goto('/reservation');
  await expect(page).toHaveURL('/reservation');
  await page.goto('/account');
  await expect(page.getByLabel('Email')).toHaveValue('alice@example.com');
  await expect(page.getByLabel('New Switch password')).toHaveCount(0);
  expect(api.calls).not.toContain('POST switch_password/');
});
