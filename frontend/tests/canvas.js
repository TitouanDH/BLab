// Driving the Topology canvas in browser tests. Cytoscape draws on a <canvas>, so tests find
// elements through the hook the page exposes in dev builds (window.__blabCanvas, see
// Topology.vue) and use them with the real mouse, at their position on screen.
import { expect } from './fixtures.js';

// Waits until the canvas has drawn this element (an id such as 'switch_2', 'link_1001')
export async function waitForElement(page, id) {
  await expect.poll(() => page.evaluate((id) => !!window.__blabCanvas?.has(id), id), { timeout: 8000 }).toBe(true);
}

export async function waitForGone(page, id) {
  await expect.poll(() => page.evaluate((id) => !!window.__blabCanvas?.has(id), id), { timeout: 8000 }).toBe(false);
}

// An element's data as the canvas holds it
export function elementData(page, id) {
  return page.evaluate((id) => window.__blabCanvas.data(id), id);
}

// Where an element is, in page coordinates: a Switch's card at its centre (or `at`, a fraction
// of its width and height from its top left corner); a Link at its middle
export async function pointOf(page, id, at = { x: 0.5, y: 0.5 }) {
  return page.evaluate(({ id, at }) => {
    const cy = window.__blabCanvas.cy;
    const element = cy.getElementById(id);
    const box = cy.container().getBoundingClientRect();
    if (element.isEdge()) {
      const mid = element.renderedMidpoint();
      return { x: box.left + mid.x, y: box.top + mid.y };
    }
    const bb = element.renderedBoundingBox({ includeLabels: false, includeOverlays: false, includeUnderlays: false });
    return { x: box.left + bb.x1 + (bb.x2 - bb.x1) * at.x, y: box.top + bb.y1 + (bb.y2 - bb.y1) * at.y };
  }, { id, at });
}

export async function clickElement(page, id) {
  await waitForElement(page, id);
  const { x, y } = await pointOf(page, id);
  await page.mouse.click(x, y);
}

export async function hoverElement(page, id) {
  await waitForElement(page, id);
  const { x, y } = await pointOf(page, id);
  await page.mouse.move(x, y);
}

// Clicks the empty canvas, away from every element
export async function clickEmpty(page) {
  const box = await page.getByTestId('topology-canvas').boundingBox();
  await page.mouse.click(box.x + box.width - 30, box.y + box.height - 30);
}

// Drags a Switch's card by this much
export async function dragSwitch(page, id, dx, dy) {
  await waitForElement(page, id);
  const { x, y } = await pointOf(page, id, { x: 0.4, y: 0.5 });
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + dx, y + dy, { steps: 8 });
  await page.mouse.up();
}

// Drags a new Link from a Switch's card (its handle, shown on hover) and drops it on another
export async function dragToConnect(page, fromId, toId) {
  await hoverElement(page, fromId);
  const handle = page.getByTestId('connect-handle');
  await expect(handle).toBeVisible();
  const box = await handle.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  const to = await pointOf(page, toId);
  await page.mouse.move(to.x, to.y, { steps: 10 });
  await page.mouse.up();
}
