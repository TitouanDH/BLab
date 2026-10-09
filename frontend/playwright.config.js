// Browser tests against the Vite dev server, with the API mocked in the page
// (tests/mock-api.js): they never reach a BLab server or a switch.
import { defineConfig, devices } from '@playwright/test';

// PLAYWRIGHT_PORT: another port when several checkouts run their tests at once
const PORT = Number(process.env.PLAYWRIGHT_PORT) || 5174;

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: 'retain-on-failure',
    ...devices['Desktop Chrome'],
    viewport: { width: 1280, height: 800 },
  },
  webServer: {
    command: `npx vite --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI,
    // No API behind the dev server's proxy: anything the mock misses fails instead of
    // reaching a real BLab
    env: { VITE_API_BASE_URL: '/api/' },
  },
});
