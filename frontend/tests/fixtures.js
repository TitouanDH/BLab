// Playwright fixtures shared by every browser test.
//
// - `api`: a MockApi (mock-api.js) answering every /api/ request in the page. It is
//   installed before the page loads, so no request ever leaves the browser.
// - `loggedInAs`: the user whose session is in localStorage when the page opens
//   ('alice' by default; null for a logged-out visitor). Set it per file or per describe:
//   test.use({ loggedInAs: null });
// A test fails if the page called an endpoint the mock doesn't know.
import { test as base, expect } from '@playwright/test';
import { MockApi } from './mock-api.js';

export const test = base.extend({
  loggedInAs: ['alice', { option: true }],

  api: async ({ page, loggedInAs }, use) => {
    const api = new MockApi();
    if (loggedInAs) {
      const user = api.state.users.find(u => u.username === loggedInAs);
      api.state.me = user;
      await page.addInitScript(({ id }) => {
        if (!sessionStorage.getItem('blab-test-session')) {
          sessionStorage.setItem('blab-test-session', '1');
          localStorage.setItem('token', `token-${id}`);
          localStorage.setItem('user', String(id));
          localStorage.setItem('is_staff', 'false');
        }
      }, user);
    }
    await api.install(page);
    await use(api);
    expect(api.unhandled, 'requests the mock API has no handler for').toEqual([]);
  },
});

export { expect };
