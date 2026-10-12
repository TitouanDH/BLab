// Lab status (#35) on the mocked API (mock-api.js): .11 clean, .12 held by alice (me), .13 held
// by bob, .14 not clean and in Quarantine naming carol, .15 Out of service; .12, .13, .15 never
// inspected.
import { test, expect } from './fixtures.js';

const rows = (page) => page.locator('tbody tr[data-row]');
const order = (page) => rows(page).evaluateAll(trs => trs.map(tr => tr.dataset.row));
const filter = (page, name) => page.getByRole('group', { name: 'Filter by state' }).getByRole('button', { name });

const SWEEP = {
  started_at: new Date().toISOString(), finished_at: new Date().toISOString(),
  summary: 'Swept 4 Switch(es): Cleaned up 1, lifted 0 Quarantine(s), 2 problem(s).',
  problems: ['10.69.145.14 is in Quarantine (names carol): Unwanted cable on 1/1/5', '10.69.145.11 could not be Cleaned up: timed out'],
};

test('the last Sweep shows only when it found something wrong, on one line until opened', async ({ page, api }) => {
  await page.goto('/status');
  await expect(rows(page)).toHaveCount(5);
  const report = page.getByRole('region', { name: 'Last Sweep report' });
  await expect(report).toHaveCount(0);

  api.state.sweep = SWEEP;
  await page.getByRole('button', { name: 'Refresh' }).click();
  await expect(report).toContainText('2 problems');
  await expect(report.getByRole('listitem').first()).toBeHidden();

  await report.getByRole('button', { name: 'Show' }).click();
  await expect(report).toContainText('Cleaned up 1');
  await expect(report.getByRole('listitem')).toHaveText(SWEEP.problems);

  // Stays open on the next visit, and closed once closed
  await page.reload();
  await expect(report.getByRole('listitem').first()).toBeVisible();
  await report.getByRole('button', { name: 'Hide' }).click();
  await page.reload();
  await expect(report).toContainText('2 problems');
  await expect(report.getByRole('listitem').first()).toBeHidden();
});

test('each sortable column sorts both ways, empty cells last', async ({ page, api }) => {
  await page.goto('/status');
  await expect(rows(page)).toHaveCount(5);
  expect(await order(page)).toEqual(['10.69.145.11', '10.69.145.12', '10.69.145.13', '10.69.145.14', '10.69.145.15']);

  const header = (name) => page.getByRole('columnheader', { name });
  await header('Switch').getByRole('button').click();
  await expect(header('Switch')).toHaveAttribute('aria-sort', 'descending');
  expect(await order(page)).toEqual(['10.69.145.15', '10.69.145.14', '10.69.145.13', '10.69.145.12', '10.69.145.11']);

  // Most urgent first: Quarantine, Out of service, mine, reserved, free
  await header('State').getByRole('button').click();
  await expect(header('State')).toHaveAttribute('aria-sort', 'ascending');
  await expect(header('Switch')).toHaveAttribute('aria-sort', 'none');
  expect(await order(page)).toEqual(['10.69.145.14', '10.69.145.15', '10.69.145.12', '10.69.145.13', '10.69.145.11']);

  await header('Holder').getByRole('button').click();
  expect(await order(page)).toEqual(['10.69.145.12', '10.69.145.13', '10.69.145.11', '10.69.145.14', '10.69.145.15']);
  await header('Holder').getByRole('button').click();
  expect(await order(page)).toEqual(['10.69.145.13', '10.69.145.12', '10.69.145.11', '10.69.145.14', '10.69.145.15']);

  await header('Until').getByRole('button').click();
  expect(await order(page)).toEqual(['10.69.145.12', '10.69.145.13', '10.69.145.11', '10.69.145.14', '10.69.145.15']);

  // Not clean, never inspected, clean
  await header('Last Inspection').getByRole('button').click();
  expect(await order(page)).toEqual(['10.69.145.14', '10.69.145.12', '10.69.145.13', '10.69.145.15', '10.69.145.11']);

  // Most findings first
  await header('Findings').getByRole('button').click();
  expect(await order(page)).toEqual(['10.69.145.14', '10.69.145.11', '10.69.145.12', '10.69.145.13', '10.69.145.15']);
});

test('the counts filter the table, and the search narrows it', async ({ page, api }) => {
  await page.goto('/status');
  await expect(rows(page)).toHaveCount(5);

  await filter(page, /never inspected/).click();
  await expect(filter(page, /never inspected/)).toHaveAttribute('aria-pressed', 'true');
  expect(await order(page)).toEqual(['10.69.145.12', '10.69.145.13', '10.69.145.15']);

  // Pressed filters add up
  await filter(page, /in Quarantine/).click();
  expect(await order(page)).toEqual(['10.69.145.12', '10.69.145.13', '10.69.145.14', '10.69.145.15']);
  await expect(page.getByTestId('shown-count')).toHaveText('4 of 5 Switches');

  await page.getByRole('searchbox', { name: 'Search Switches' }).fill('bob');
  expect(await order(page)).toEqual(['10.69.145.13']);

  await page.getByRole('searchbox', { name: 'Search Switches' }).fill('nobody');
  await expect(rows(page)).toHaveCount(0);
  await page.getByRole('button', { name: 'Clear filters' }).last().click();
  await expect(rows(page)).toHaveCount(5);
  await expect(filter(page, /never inspected/)).toHaveAttribute('aria-pressed', 'false');
});

test('Findings show the first one, the rest in the details', async ({ page, api }) => {
  api.state.inspections[4].reasons = ['Unwanted cable on 1/1/5', 'Unwanted cable on 1/1/6'];
  api.state.inspections[4].warnings = ['A local user BLab did not make: test'];
  await page.goto('/status');
  const findings = page.locator('[data-row="10.69.145.14"]').getByTestId('findings');
  await expect(findings).toContainText('Unwanted cable on 1/1/5');
  await expect(findings).not.toContainText('1/1/6');

  await findings.getByRole('button', { name: '2 more findings' }).click();
  const details = page.getByTestId('details');
  await expect(details.getByTestId('all-findings').getByRole('listitem')).toHaveText(['Unwanted cable on 1/1/5', 'Unwanted cable on 1/1/6', 'A local user BLab did not make: test']);
  await expect(details).toContainText('A local user BLab did not make: test');
  await expect(details).toContainText('History');
});

test('a Quarantine shows its own reasons, whatever the last Inspection found', async ({ page, api }) => {
  api.state.inspections[4] = { kind: 'inspection', at: new Date().toISOString(), ok: true, reasons: [], warnings: [], user: null };
  await page.goto('/status');
  await expect(page.locator('[data-switch="10.69.145.14"]')).toContainText('Unwanted cable on 1/1/5');
});
