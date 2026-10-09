// The cytoscape stylesheet of the Topology canvas, built from the state tables in model.js
// so the canvas and the legend can't disagree.
import { COLORS, LINK_STATES, PORT_STATES, SWITCH_STATES } from './model.js';

const PRIMARY_600 = COLORS.primary[600];
const PRIMARY_800 = COLORS.primary[800];
const SELECTED = COLORS.primary[500];

const switchStates = Object.entries(SWITCH_STATES).map(([state, s]) => ({
  selector: `node[type="switch"][state="${state}"]`,
  style: { 'background-color': s.fill, 'border-color': s.color, 'border-style': s.border, 'border-width': s.width },
}));

const portStates = Object.entries(PORT_STATES).map(([state, s]) => ({
  selector: `node[type="port"][state="${state}"]`,
  style: { 'background-color': s.fill, 'border-color': s.color },
}));

const linkStates = Object.entries(LINK_STATES).map(([state, s]) => ({
  selector: `edge[state="${state}"]`,
  style: {
    'line-color': s.color,
    'width': s.width,
    'line-style': s.dash ? 'dashed' : 'solid',
    ...(s.dash ? { 'line-dash-pattern': s.dash } : {}),
  },
}));

export const canvasStyle = [
  {
    selector: 'node[type="switch"]',
    style: {
      'shape': 'round-rectangle',
      'label': 'data(label)',
      'text-wrap': 'wrap',
      'text-valign': 'top',
      'text-halign': 'center',
      'text-margin-y': -6,
      'font-size': 12,
      'font-weight': 'bold',
      'color': COLORS.gray[900],
      'padding': '14px',
      // A Switch without ports still shows as a box
      'width': 112,
      'height': 40,
      // The label is part of the Switch: clicking or dragging it works too
      'text-events': 'yes',
    },
  },
  { selector: 'node[type="switch"][state="outside"]', style: { 'color': COLORS.gray[500] } },
  ...switchStates,
  {
    selector: 'node[type="port"]',
    style: {
      'shape': 'round-rectangle',
      'width': 18,
      'height': 18,
      'border-width': 2,
      'label': 'data(label)',
      'font-size': 10,
      'color': COLORS.gray[700],
      'text-valign': 'bottom',
      'text-halign': 'center',
      'text-margin-y': 4,
    },
  },
  ...portStates,
  {
    selector: 'edge',
    style: {
      'curve-style': 'bezier',
      'width': 3,
      'line-cap': 'round',
    },
  },
  ...linkStates,
  // A Link that would pass over another port (canvas.js)
  { selector: 'edge.arched', style: { 'curve-style': 'unbundled-bezier', 'control-point-distances': 100, 'control-point-weights': 0.5 } },
  { selector: 'edge.hover', style: { 'underlay-color': SELECTED, 'underlay-opacity': 0.25, 'underlay-padding': 4 } },
  // Connect mode: the port it starts from, and the ports it may go to
  { selector: 'node.connect-source', style: { 'background-color': PRIMARY_600, 'border-color': PRIMARY_800, 'border-width': 3 } },
  { selector: 'node.connect-target', style: { 'border-color': PRIMARY_600, 'border-width': 3, 'underlay-color': SELECTED, 'underlay-opacity': 0.2, 'underlay-padding': 4 } },
  // Cytoscape's own grey press feedback would hide the states
  { selector: 'node:active, edge:active', style: { 'overlay-opacity': 0 } },
  // The element shown in the side panel
  { selector: '.selected', style: { 'overlay-color': SELECTED, 'overlay-opacity': 0.25, 'overlay-padding': 6 } },
];
