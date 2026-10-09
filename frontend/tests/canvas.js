// Driving the Topology canvas in browser tests. Cytoscape draws on a <canvas>, so tests find
// elements through the hook the page exposes in dev builds (window.__blabCanvas, see
// Topology.vue) and click them with the real mouse, at their position on screen.
import { expect } from './fixtures.js';

// Waits until the canvas has drawn this element (an id such as 'port_22', 'link_1001')
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

// Where to click an element, in page coordinates. A Switch is clicked on its frame, above its
// ports; a Link at its middle.
async function pointOf(page, id) {
  return page.evaluate((id) => {
    const cy = window.__blabCanvas.cy;
    const element = cy.getElementById(id);
    const box = cy.container().getBoundingClientRect();
    if (element.isEdge()) {
      const mid = element.renderedMidpoint();
      return { x: box.left + mid.x, y: box.top + mid.y };
    }
    const bb = element.renderedBoundingBox({ includeLabels: false });
    const y = element.data('type') === 'switch' ? bb.y1 + 5 : (bb.y1 + bb.y2) / 2;
    return { x: box.left + (bb.x1 + bb.x2) / 2, y: box.top + y };
  }, id);
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
