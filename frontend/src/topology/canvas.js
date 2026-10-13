// The Topology canvas: cytoscape behind a small interface. The page hands it the elements
// to draw (model.js buildElements) on every refresh; the canvas adds, updates and removes
// only what changed, so a refresh never moves anything, never resets the viewport, and
// keeps the selection and the Switches highlighted for a new Link.
//
//   const canvas = createCanvas(container, { onTap, onHover, onHoverSwitch, onViewChange, onSwitchesMoved });
//   canvas.sync(elements, savedPositions);   // savedPositions: { [switchId]: {x, y} }
//   canvas.select('switch_2'); canvas.setConnect({ source: 'switch_2', targets: [...], drop: null });
//   canvas.fit(); canvas.zoomBy(1.25); canvas.rearrange();
import cytoscape from 'cytoscape';
import { SWITCH_SIZE, tidySwitchPositions } from './model.js';
import { canvasStyle } from './style.js';

const FIT_PADDING = 50;
const MAX_FIT_ZOOM = 1.25;  // a small Topology isn't blown up to fill the screen
const GAP_BESIDE = 200;     // between what is drawn and Switches added later

/**
 * @param {HTMLElement} container
 * @param {object} handlers
 *   onTap(data | null): an element was clicked (its data), or the empty canvas (null)
 *   onHover({ data, x, y } | null): the pointer is over a Link (x, y in the container), or left it
 *   onHoverSwitch(id | null): the pointer is over a Switch's card, or left it
 *   onViewChange(): the view moved (pan, zoom, a Switch dragged): what sits over it follows
 *   onSwitchesMoved(positions): a drag ended; positions of every Switch, by Switch id
 */
export function createCanvas(container, { onTap, onHover, onHoverSwitch = () => {}, onViewChange = () => {}, onSwitchesMoved }) {
  const cy = cytoscape({
    container,
    style: canvasStyle,
    layout: { name: 'preset' },
    minZoom: 0.2,
    maxZoom: 3,
    wheelSensitivity: 0.3,
    boxSelectionEnabled: false,
    autounselectify: true,  // the page keeps the selection (select())
  });
  let selectedId = null;
  let connect = null;  // { source, targets, drop }

  cy.on('tap', (event) => onTap(event.target === cy ? null : event.target.data()));
  cy.on('mouseover', 'edge', (event) => {
    event.target.addClass('hover');
    onHover({ data: event.target.data(), ...event.renderedPosition });
  });
  cy.on('mouseout', 'edge', (event) => {
    event.target.removeClass('hover');
    onHover(null);
  });
  cy.on('mouseover', 'node[type="switch"]', (event) => onHoverSwitch(event.target.id()));
  cy.on('mouseout', 'node[type="switch"]', () => onHoverSwitch(null));
  cy.on('dragfree', 'node[type="switch"]', () => onSwitchesMoved(switchPositions()));
  cy.on('viewport position', () => onViewChange());
  // The side panel opening or closing changes the container's width: the view keeps its place
  const resizes = new ResizeObserver(() => {
    cy.resize();
    onViewChange();
  });
  resizes.observe(container);

  function switchPositions() {
    return Object.fromEntries(cy.nodes('[type="switch"]').map(n => [n.data('switchId'), { ...n.position() }]));
  }

  // Keeps an element's data in step, removing what the new data no longer has
  function update(element, data) {
    const { id, source, target, ...rest } = data;
    for (const key of Object.keys(element.data())) {
      if (!(key in data)) element.removeData(key);
    }
    element.data(rest);
  }

  function applyClasses() {
    cy.elements().removeClass('selected connect-source connect-target drop-target');
    if (selectedId) cy.getElementById(selectedId).addClass('selected');
    if (connect) {
      cy.getElementById(connect.source).addClass('connect-source');
      for (const id of connect.targets) cy.getElementById(id).addClass('connect-target');
      if (connect.drop) cy.getElementById(connect.drop).addClass('drop-target');
    }
  }

  return {
    cy,

    /**
     * Draws these elements. Switches already drawn stay where they are; new ones go to their
     * saved position, or are laid out tidily (beside what is drawn, if anything is).
     * @param {{ switches, links }} elements - from model.js buildElements
     * @param {{ [switchId]: {x, y} }} saved - saved Switch positions
     */
    sync({ switches, links }, saved = {}) {
      const wanted = new Set([...switches, ...links].map(e => e.data.id));
      cy.batch(() => {
        cy.edges().filter(e => !wanted.has(e.id())).remove();
        cy.nodes().filter(n => !wanted.has(n.id())).remove();
        // An edge id whose ends changed is another Link
        for (const def of links) {
          const edge = cy.getElementById(def.data.id);
          if (edge.nonempty() && (edge.data('source') !== def.data.source || edge.data('target') !== def.data.target)) {
            edge.remove();
          }
        }

        const unsaved = [];
        for (const def of switches) {
          const node = cy.getElementById(def.data.id);
          if (node.nonempty()) {
            update(node, def.data);
          } else {
            const position = saved[def.data.switchId];
            cy.add({ ...def, position: position ? { ...position } : { x: 0, y: 0 } });
            if (!position) unsaved.push(def.data.id);
          }
        }
        // The others tidily beside whatever is drawn
        if (unsaved.length) {
          const drawn = cy.nodes().filter(n => !unsaved.includes(n.id()));
          const box = drawn.nonempty() ? drawn.boundingBox({ includeLabels: false }) : null;
          const tidy = tidySwitchPositions(unsaved);
          const offset = box
            ? { x: box.x2 + GAP_BESIDE + SWITCH_SIZE.width / 2, y: box.y1 + SWITCH_SIZE.height / 2 }
            : { x: 0, y: 0 };
          for (const id of unsaved) cy.getElementById(id).position({ x: tidy[id].x + offset.x, y: tidy[id].y + offset.y });
        }

        for (const def of links) {
          const edge = cy.getElementById(def.data.id);
          if (edge.nonempty()) update(edge, def.data);
          else if (cy.getElementById(def.data.source).nonempty() && cy.getElementById(def.data.target).nonempty()) cy.add(def);
        }
      });
      if (selectedId && cy.getElementById(selectedId).empty()) selectedId = null;
      applyClasses();
    },

    /** Removes everything, e.g. before drawing another Topology */
    clear() {
      cy.elements().remove();
      selectedId = null;
      connect = null;
    },

    has: (id) => !!id && cy.getElementById(id).nonempty(),
    data: (id) => (id && cy.getElementById(id).nonempty() ? cy.getElementById(id).data() : null),
    // Every Switch and every Link drawn, as data
    switches: () => cy.nodes('[type="switch"]').map(n => n.data()),
    links: () => cy.edges().map(e => e.data()),
    // The element definitions of a Link and both its Switches, to keep drawing it after the
    // API stops listing it (model.js buildElements, disconnecting)
    snapshot: (edgeId) => {
      const edge = cy.getElementById(edgeId);
      return { edge: { data: { ...edge.data() } }, nodes: edge.connectedNodes().map(n => ({ data: { ...n.data() } })) };
    },

    /** Where an element is drawn, in the container's pixels: { x1, y1, x2, y2 } */
    renderedBox: (id) => {
      const element = id && cy.getElementById(id);
      return element?.nonempty() ? element.renderedBoundingBox({ includeLabels: false, includeOverlays: false, includeUnderlays: false }) : null;
    },

    /** The Switch drawn at this point of the container, as data, or null */
    switchAt(x, y) {
      const node = cy.nodes('[type="switch"]').filter((n) => {
        const bb = n.renderedBoundingBox({ includeLabels: false, includeOverlays: false, includeUnderlays: false });
        return x >= bb.x1 && x <= bb.x2 && y >= bb.y1 && y <= bb.y2;
      }).last();  // the one drawn on top
      return node.nonempty() ? node.data() : null;
    },

    select(id) {
      selectedId = id;
      applyClasses();
    },

    /** A new Link being dragged: the Switch it starts from, those it may end on, the one under
     * the pointer; null when done */
    setConnect(value) {
      connect = value;
      applyClasses();
    },

    fit() {
      if (cy.elements().empty()) return;
      cy.fit(cy.elements(), FIT_PADDING);
      if (cy.zoom() > MAX_FIT_ZOOM) {
        cy.zoom(MAX_FIT_ZOOM);
        cy.center();
      }
    },

    zoomBy(factor) {
      cy.zoom({ level: cy.zoom() * factor, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
    },

    /** The tidy layout again, for every Switch drawn */
    rearrange() {
      const nodes = cy.nodes('[type="switch"]');
      const tidy = tidySwitchPositions(nodes.map(n => n.id()));
      cy.batch(() => nodes.forEach(n => n.position(tidy[n.id()])));
      this.fit();
    },

    switchPositions,

    destroy() {
      resizes.disconnect();
      cy.destroy();
    },
  };
}
