// Every account needs an email, the user's Rainbow login (#23): sign-up asks for it, and an
// existing account without one is sent to the account page until it is set.
import { test, expect } from './fixtures.js';

test.describe('logged out', () => {
  test.use({ loggedInAs: null });

  test('sign-up requires an email and sends it', async ({ page, api }) => {
    await page.goto('/signup');
    await page.getByLabel('Username').fill('dave');
    await page.getByLabel('Password').fill('Secret123');
    await page.getByRole('button', { name: 'Sign up' }).click();
    // The browser refuses the form without an email: nothing reaches BLab
    await expect(page).toHaveURL('/signup');
    expect(api.calls).not.toContain('POST signup/');

    await page.getByLabel('Email').fill('not-an-email');
    await page.getByRole('button', { name: 'Sign up' }).click();
    await expect(page).toHaveURL('/signup');
    expect(api.calls).not.toContain('POST signup/');

    await page.getByLabel('Email').fill('Dave@Example.com');
    await page.getByRole('button', { name: 'Sign up' }).click();
    await expect(page).toHaveURL('/login');
    expect(api.state.users.find(u => u.username === 'dave').email).toBe('dave@example.com');
  });

  test('a refused email says why', async ({ page, api }) => {
    api.override('POST', 'signup/', () => ({ status: 400, body: { email: ['Another account already uses this email address.'] } }));
    await page.goto('/signup');
    await page.getByLabel('Username').fill('dave');
    await page.getByLabel('Email').fill('alice@example.com');
    await page.getByLabel('Password').fill('Secret123');
    await page.getByRole('button', { name: 'Sign up' }).click();
    await expect(page.getByRole('alert')).toHaveText('Another account already uses this email address.');
    await expect(page).toHaveURL('/signup');
  });

  test('logging in to an account without an email leads to the email form, then on', async ({ page, api }) => {
    api.state.users.find(u => u.username === 'alice').email = '';
    await page.goto('/login');
    await page.getByLabel('Username').fill('alice');
    await page.getByLabel('Password').fill('secret');
    await page.getByRole('button', { name: 'Log in' }).click();
    await expect(page).toHaveURL('/account');
    await expect(page.getByText('BLab needs your email address before you go on')).toBeVisible();

    await page.getByLabel('Email').fill('alice@example.com');
    await page.getByRole('button', { name: 'Save' }).click();
    await expect(page).toHaveURL('/');
    await expect(page.getByRole('heading', { name: 'My lab' })).toBeVisible();
    expect(api.state.me.email).toBe('alice@example.com');
  });
});

test('an existing session without an email is sent to the email form from every page', async ({ page, api }) => {
  api.state.me.email = '';
  for (const path of ['/', '/reservation', '/topology', '/status']) {
    await page.goto(path);
    await expect(page).toHaveURL('/account');
  }
  await page.getByRole('link', { name: 'My lab' }).click();
  await expect(page).toHaveURL('/account');
});

test('when BLab refuses for a missing email, the page goes to the email form', async ({ page, api }) => {
  await page.goto('/reservation');
  await expect(page.locator('[data-switch]').first()).toBeVisible();
  api.state.me.email = '';  // say an admin emptied it meanwhile
  await page.reload();  // this browser still remembers the email: the page loads, BLab refuses it
  await expect(page).toHaveURL('/account');
  await expect(page.getByLabel('Email')).toHaveValue('');
});

test('the email can be changed later from the account page', async ({ page, api }) => {
  await page.goto('/');
  await page.getByRole('link', { name: 'Account' }).click();
  await expect(page).toHaveURL('/account');
  await expect(page.getByText('alice', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Email')).toHaveValue('alice@example.com');
  await expect(page.getByText('BLab needs your email address')).toBeHidden();
  await page.getByLabel('Email').fill('alice.smith@example.com');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Email saved.' })).toBeVisible();
  await expect(page).toHaveURL('/account');
  expect(api.state.me.email).toBe('alice.smith@example.com');
});
