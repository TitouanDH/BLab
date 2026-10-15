// The cytoscape stylesheet of the Topology canvas, built from the state tables in model.js
// so the canvas and the legend can't disagree. A Switch is its card (cards.js), painted as
// the node's image; a Link goes from card to card with its port as a label at each end.
import { COLORS, MONO } from './colors.js';
import { LINK_STATES, SWITCH_SIZE } from './model.js';

const PRIMARY_600 = COLORS.primary[600];
const SELECTED = COLORS.primary[500];

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
      'width': SWITCH_SIZE.width,
      'height': SWITCH_SIZE.height,
      'background-opacity': 0,
      'border-width': 0,
      'background-image': 'data(card)',
      'background-fit': 'contain',
      'background-clip': 'none',
      // The card's shadow
      'underlay-color': COLORS.gray[900],
      'underlay-opacity': 0.06,
      'underlay-padding': 3,
      'underlay-shape': 'round-rectangle',
    },
  },
  {
    selector: 'edge',
    style: {
      'curve-style': 'bezier',
      'control-point-step-size': 46,  // parallel Links fan out
      'line-cap': 'round',
      // The port at each end
      'source-label': 'data(sourceLabel)',
      'target-label': 'data(targetLabel)',
      'source-text-offset': 30,
      'target-text-offset': 30,
      'font-family': MONO,
      'font-size': 10,
      'color': COLORS.gray[800],
      'text-background-color': '#ffffff',
      'text-background-opacity': 1,
      'text-background-padding': 2,
      'text-background-shape': 'round-rectangle',
      'text-border-color': COLORS.gray[300],
      'text-border-width': 1,
      'text-border-opacity': 1,
    },
  },
  ...linkStates,
  // Both ends on one Switch: a loop beside the card
  { selector: 'edge:loop', style: { 'loop-direction': '90deg', 'loop-sweep': '-50deg', 'control-point-step-size': 70 } },
  { selector: 'edge.hover', style: { 'underlay-color': SELECTED, 'underlay-opacity': 0.25, 'underlay-padding': 4 } },
  // Dragging a new Link: where it starts, and the Switches it may end on
  { selector: 'node.connect-source', style: { 'underlay-color': PRIMARY_600, 'underlay-opacity': 0.35, 'underlay-padding': 6 } },
  { selector: 'node.connect-target', style: { 'underlay-color': SELECTED, 'underlay-opacity': 0.2, 'underlay-padding': 6 } },
  { selector: 'node.drop-target', style: { 'underlay-color': PRIMARY_600, 'underlay-opacity': 0.45, 'underlay-padding': 8 } },
  // Cytoscape's own grey press feedback would hide the states
  { selector: 'node:active, edge:active', style: { 'overlay-opacity': 0 } },
  // The element shown in the side panel
  { selector: '.selected', style: { 'overlay-color': SELECTED, 'overlay-opacity': 0.2, 'overlay-padding': 6 } },
];
