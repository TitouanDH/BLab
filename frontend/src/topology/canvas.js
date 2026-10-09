// The Topology canvas: cytoscape behind a small interface. The page hands it the elements
// to draw (model.js buildElements) on every refresh; the canvas adds, updates and removes
// only what changed, so a refresh never moves anything, never resets the viewport, and
// keeps the selection and the connect mode.
//
//   const canvas = createCanvas(container, { onTap, onHover, onSwitchesMoved });
//   canvas.sync(elements, savedPositions);   // savedPositions: { [switchId]: {x, y} }
//   canvas.select('port_12'); canvas.setConnect({ source: 'port_12', targets: [...] });
//   canvas.fit(); canvas.zoomBy(1.25); canvas.rearrange();
import cytoscape from 'cytoscape';
import { portOffsets, switchSize, tidySwitchPositions } from './model.js';
import { canvasStyle } from './style.js';

const FIT_PADDING = 50;
const MAX_FIT_ZOOM = 1.25;  // a small Topology isn't blown up to fill the screen
const GAP_BESIDE = 200;     // between what is drawn and Switches added later

/**
 * @param {HTMLElement} container
 * @param {object} handlers
 *   onTap(data | null): an element was clicked (its data), or the empty canvas (null)
 *   onHover({ data, x, y } | null): the pointer is over a Link (x, y in the container), or left it
 *   onSwitchesMoved(positions): a drag ended; positions of every Switch, by Switch id
 */
export function createCanvas(container, { onTap, onHover, onSwitchesMoved }) {
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
  let connect = null;  // { source, targets }

  cy.on('tap', (event) => onTap(event.target === cy ? null : event.target.data()));
  cy.on('mouseover', 'edge', (event) => {
    event.target.addClass('hover');
    onHover({ data: event.target.data(), ...event.renderedPosition });
  });
  cy.on('mouseout', 'edge', (event) => {
    event.target.removeClass('hover');
    onHover(null);
  });
  cy.on('dragfree', 'node[type="switch"]', () => onSwitchesMoved(switchPositions()));
  cy.on('position', 'node', () => scheduleArches());

  // A straight Link passing over another port would look connected to it: such a Link is
  // drawn as an arch instead (style.js, edge.arched)
  let archesScheduled = false;
  function scheduleArches() {
    if (archesScheduled) return;
    archesScheduled = true;
    requestAnimationFrame(() => {
      archesScheduled = false;
      if (!cy.destroyed()) updateArches();
    });
  }
  function updateArches() {
    const ports = cy.nodes('[type="port"]').map(n => ({ id: n.id(), ...n.position() }));
    cy.edges().forEach(edge => {
      const a = edge.source().position();
      const b = edge.target().position();
      const crosses = ports.some(p => p.id !== edge.source().id() && p.id !== edge.target().id()
        && distanceToSegment(p, a, b) < ARCH_CLEARANCE);
      if (crosses !== edge.hasClass('arched')) edge.toggleClass('arched', crosses);
    });
  }

  // A Switch's position: the centre of its ports, which place() puts them around. Not the
  // compound node's own position, whose box also holds the port names.
  function anchorOf(switchNode) {
    const ports = switchNode.children();
    return ports.empty() ? { ...switchNode.position() } : centerOf(ports.boundingBox({ includeLabels: false }));
  }

  function switchPositions() {
    return Object.fromEntries(cy.nodes('[type="switch"]').map(n => [n.data('switchId'), anchorOf(n)]));
  }

  // Puts a Switch's ports (or the Switch itself, without ports) around a position
  function place(switchNode, anchor) {
    const ports = switchNode.children().sort((a, b) => a.data('portId') - b.data('portId'));
    if (ports.empty()) {
      switchNode.position(anchor);
      return;
    }
    const offsets = portOffsets(ports.length);
    ports.forEach((port, i) => port.position({ x: anchor.x + offsets[i].x, y: anchor.y + offsets[i].y }));
  }

  // Keeps an element's data in step, removing what the new data no longer has
  function update(element, data) {
    const { id, parent, source, target, ...rest } = data;
    for (const key of Object.keys(element.data())) {
      if (!(key in data)) element.removeData(key);
    }
    element.data(rest);
  }

  function applyClasses() {
    cy.elements().removeClass('selected connect-source connect-target');
    if (selectedId) cy.getElementById(selectedId).addClass('selected');
    if (connect) {
      cy.getElementById(connect.source).addClass('connect-source');
      for (const id of connect.targets) cy.getElementById(id).addClass('connect-target');
    }
  }

  return {
    cy,

    /**
     * Draws these elements. Switches already drawn stay where they are; new ones go to their
     * saved position, or are laid out tidily (beside what is drawn, if anything is).
     * @param {{ switches, ports, links }} elements - from model.js buildElements
     * @param {{ [switchId]: {x, y} }} saved - saved Switch positions
     */
    sync({ switches, ports, links }, saved = {}) {
      const wanted = new Set([...switches, ...ports, ...links].map(e => e.data.id));
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

        const newSwitches = switches.filter(def => cy.getElementById(def.data.id).empty());
        const changedSwitches = new Set();
        const newPorts = new Set();
        for (const def of [...switches, ...ports]) {
          const element = cy.getElementById(def.data.id);
          if (element.nonempty()) {
            update(element, def.data);
          } else {
            cy.add({ ...def, grabbable: def.data.type === 'switch' });
            if (def.data.type === 'port') {
              changedSwitches.add(def.data.parent);
              newPorts.add(def.data.id);
            }
          }
        }

        // Positions: saved ones first, then the others tidily beside whatever is drawn
        const newIds = new Set(newSwitches.map(def => def.data.id));
        const unsaved = [];
        for (const def of newSwitches) {
          const position = saved[def.data.switchId];
          if (position) place(cy.getElementById(def.data.id), position);
          else unsaved.push(def);
        }
        // Switches drawn before that got a new port: their ports are laid out again in place
        for (const id of changedSwitches) {
          if (newIds.has(id)) continue;
          const node = cy.getElementById(id);
          const before = node.children().filter(p => !newPorts.has(p.id()));
          place(node, before.nonempty() ? centerOf(before.boundingBox({ includeLabels: false })) : { ...node.position() });
        }
        if (unsaved.length) {
          const drawn = cy.nodes().filter(n => !unsaved.some(def => def.data.id === n.id() || def.data.id === n.data('parent')));
          const box = drawn.nonempty() ? drawn.boundingBox({ includeLabels: false }) : null;
          const sizes = unsaved.map(def => ({
            id: def.data.id,
            portCount: cy.getElementById(def.data.id).children().length,
          }));
          const tidy = tidySwitchPositions(sizes);
          const first = sizes.length ? switchSize(sizes[0].portCount) : { width: 0, height: 0 };
          const offset = box ? { x: box.x2 + GAP_BESIDE + first.width / 2, y: box.y1 + first.height / 2 } : { x: 0, y: 0 };
          for (const { id } of sizes) {
            place(cy.getElementById(id), { x: tidy[id].x + offset.x, y: tidy[id].y + offset.y });
          }
        }

        for (const def of links) {
          const edge = cy.getElementById(def.data.id);
          if (edge.nonempty()) update(edge, def.data);
          else if (cy.getElementById(def.data.source).nonempty() && cy.getElementById(def.data.target).nonempty()) cy.add(def);
        }
      });
      if (selectedId && cy.getElementById(selectedId).empty()) selectedId = null;
      applyClasses();
      updateArches();
    },

    /** Removes everything, e.g. before drawing another Topology */
    clear() {
      cy.elements().remove();
      selectedId = null;
      connect = null;
    },

    has: (id) => !!id && cy.getElementById(id).nonempty(),
    // Every port drawn, as data
    ports: () => cy.nodes('[type="port"]').map(n => n.data()),
    // The Link drawn on a port, as data, or null
    linkOf: (portId) => {
      const edge = cy.getElementById(portId).connectedEdges().first();
      return edge.nonempty() ? edge.data() : null;
    },
    data: (id) => (id && cy.getElementById(id).nonempty() ? cy.getElementById(id).data() : null),
    // A Link's ends, as port data
    ends: (edgeId) => {
      const edge = cy.getElementById(edgeId);
      return edge.nonempty() ? [edge.source().data(), edge.target().data()] : [];
    },
    // The element definitions of a Link and both its ends, to keep drawing it after the
    // API stops listing it (model.js buildElements, disconnecting)
    snapshot: (edgeId) => {
      const edge = cy.getElementById(edgeId);
      const nodes = edge.connectedNodes().union(edge.connectedNodes().parents());
      return { edge: { data: { ...edge.data() } }, nodes: nodes.map(n => ({ data: { ...n.data() } })) };
    },

    select(id) {
      selectedId = id;
      applyClasses();
    },

    /** Connect mode: the port it starts from and the ports it may end on; null to leave it */
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
      const tidy = tidySwitchPositions(nodes.map(n => ({ id: n.id(), portCount: n.children().length })));
      cy.batch(() => nodes.forEach(n => place(n, tidy[n.id()])));
      this.fit();
    },

    switchPositions,

    destroy() {
      cy.destroy();
    },
  };
}

const centerOf = (box) => ({ x: (box.x1 + box.x2) / 2, y: (box.y1 + box.y2) / 2 });

const ARCH_CLEARANCE = 16;  // from a port's centre: its half width and a little

function distanceToSegment(p, a, b) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const length2 = dx * dx + dy * dy;
  const t = length2 ? Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / length2)) : 0;
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}
