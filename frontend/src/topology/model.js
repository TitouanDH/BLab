// What the Topology canvas draws, worked out from the API's answer: the state of each Switch,
// port and Link, the cytoscape elements, and the tidy layout. No cytoscape and no Vue here,
// so the rules stay in one place: the canvas style (style.js) and the legend
// (TopologyLegend.vue) are both built from the state tables below.
//
// Every state is a colour *plus* a line or border style, so it reads without colour vision.
// Cytoscape needs colour values, so they come from the same palettes as the Tailwind tokens
// (tailwind.config.js): primary is teal, warning is amber. Red is for destructive actions
// only, never a state.
import colors from 'tailwindcss/colors';

export const COLORS = {
  primary: colors.teal,
  warning: colors.amber,
  gray: colors.gray,
};
const GRAY_700 = COLORS.gray[700];
const GRAY_400 = COLORS.gray[400];
const PRIMARY_600 = COLORS.primary[600];
const PRIMARY_700 = COLORS.primary[700];
const WARNING_600 = COLORS.warning[600];
const WARNING_700 = COLORS.warning[700];

export const ENDING_SOON_MS = 2 * 24 * 60 * 60 * 1000;

// Switch states, in the legend's order. border: a CSS border style, which cytoscape shares.
// badge and tone: how the side panel names it, in a UiBadge.
export const SWITCH_STATES = {
  topology: { label: 'Switch in this Topology', fill: '#ffffff', color: PRIMARY_700, border: 'solid', width: 2,
    badge: 'In this Topology', tone: 'neutral' },
  outside: { label: 'Switch outside this Topology', fill: COLORS.gray[100], color: GRAY_400, border: 'dashed', width: 2,
    badge: 'Outside this Topology', tone: 'neutral' },
  ending: { label: 'Reservation ends in under 2 days', fill: '#ffffff', color: WARNING_600, border: 'double', width: 6,
    badge: 'Reservation ends in under 2 days', tone: 'warning' },
  quarantine: { label: 'In Quarantine', fill: COLORS.warning[100], color: WARNING_700, border: 'dotted', width: 4,
    badge: 'In Quarantine', tone: 'strong-warning' },
};

// Port states: hollow when free, filled when it has a Link
export const PORT_STATES = {
  free: { label: 'Port', fill: '#ffffff', color: GRAY_700 },
  linked: { label: 'Port with a Link', fill: GRAY_700, color: GRAY_700 },
};

// Link states, in the legend's order. dash: the dash pattern (null for a plain line), used by
// cytoscape's line-dash-pattern and by the legend's SVG stroke-dasharray alike.
// tone: the side panel's UiBadge (none for a plain Link). A Ghost Link (#30) gets its own
// entry here.
export const LINK_STATES = {
  up: { label: 'Link', color: GRAY_700, dash: null, width: 3, tone: null },
  connecting: { label: 'Being connected', color: PRIMARY_600, dash: [10, 6], width: 3, tone: 'primary' },
  disconnecting: { label: 'Being disconnected', color: GRAY_400, dash: [2, 5], width: 3, tone: 'neutral' },
  failed: { label: 'Disconnect failed', color: WARNING_600, dash: [12, 4, 3, 4], width: 4, tone: 'warning' },
};

// --- Ids: one place for the canvas ids and the API ids behind them ---

export const switchNodeId = (switchId) => `switch_${switchId}`;
export const portNodeId = (portId) => `port_${portId}`;
export const linkEdgeId = (svlan) => `link_${svlan}`;
export const connectingEdgeId = (portA, portB) => `connecting_${portA}_${portB}`;

export const switchName = (sw) => `${sw.model} (${sw.mngt_IP})`;
// How the page names a port: "1/1/2 on OS6860E-24 (10.69.145.11)"
export const portFullName = (port, sw) => `${port.port_switch} on ${switchName(sw)}`;

/**
 * The state a Switch is drawn in. A Quarantine wins over everything, then a Reservation
 * ending soon (only for a Switch of the Topology: its holder can act on it).
 */
export function switchState(sw, { quarantined = false, now = Date.now() } = {}) {
  if (quarantined) return 'quarantine';
  if (!sw.in_topology) return 'outside';
  const end = sw.reservation?.end_date ? new Date(sw.reservation.end_date).getTime() : null;
  if (end !== null && end - now < ENDING_SOON_MS) return 'ending';
  return 'topology';
}

// A disconnect asked for and not done yet (api/api/models.py Port.teardown_pending)
const teardownPending = (port) =>
  port.svlan != null && port.teardown_requested_at != null && port.teardown_svlan === port.svlan;

/**
 * The elements to draw, without positions.
 *
 * @param {object} topology - the API's answer: { switches, ports, links }
 * @param {object} extra
 *   quarantines: Set of Switch ids in Quarantine (from the Lab status)
 *   holders: { [switchId]: username }
 *   connecting: { portA, portB } while a connect request runs, or null
 *   disconnecting: Map svlan -> { edge, nodes }: Links this page asked to disconnect, as they
 *     were drawn. The API hides a Link being disconnected (unless its teardown failed), and
 *     with it the far end of a Link leaving the Topology; this keeps them drawn meanwhile.
 *     Entries no longer needed are deleted from the Map.
 *   inFlight: Set of SVLANs whose disconnect request is still running
 * @returns {{ switches: object[], ports: object[], links: object[] }} cytoscape element
 *   definitions ({ data }) by kind
 */
export function buildElements(topology, {
  quarantines = new Set(), holders = {}, connecting = null, disconnecting = new Map(),
  inFlight = new Set(), now = Date.now(),
} = {}) {
  const switchesById = new Map(topology.switches.map(sw => [sw.id, sw]));
  const shownSvlans = new Set(topology.links.map(l => l.svlan));
  const portsById = new Map(topology.ports.map(p => [p.id, p]));

  const switches = topology.switches.map(sw => ({
    data: {
      id: switchNodeId(sw.id),
      type: 'switch',
      switchId: sw.id,
      label: `${sw.model}\n${sw.mngt_IP}`,
      name: switchName(sw),
      model: sw.model,
      ip: sw.mngt_IP,
      console: sw.console,
      holder: holders[sw.id] || null,
      inTopology: !!sw.in_topology,
      endDate: sw.reservation?.end_date || null,
      renewalsLeft: sw.reservation?.renewals_left ?? null,
      state: switchState(sw, { quarantined: quarantines.has(sw.id), now }),
    },
  }));

  const ports = topology.ports.filter(p => switchesById.has(p.switch)).map(port => {
    const sw = switchesById.get(port.switch);
    return {
      data: {
        id: portNodeId(port.id),
        type: 'port',
        portId: port.id,
        parent: switchNodeId(sw.id),
        label: port.port_switch,
        fullName: portFullName(port, sw),
        backbone: port.backbone,
        uni: port.port_backbone,
        inTopology: !!sw.in_topology,
        svlan: port.svlan ?? null,
        teardownPending: teardownPending(port),
        state: port.svlan != null ? 'linked' : 'free',
      },
    };
  });

  const linkEdge = (svlan, [a, b], state, teardownError = null) => ({
    data: {
      id: linkEdgeId(svlan),
      type: 'link',
      svlan,
      source: portNodeId(a),
      target: portNodeId(b),
      state,
      // Set only when a disconnect failed
      ...(teardownError ? { teardownError } : {}),
    },
  });

  const links = topology.links.map(link => {
    const kept = disconnecting.get(link.svlan);
    const sameEnds = (edge) => [edge.data.source, edge.data.target].sort().join()
      === link.ports.map(portNodeId).sort().join();
    // The SVLAN now carries a new Link: the one asked for is gone
    if (kept && !sameEnds(kept.edge)) disconnecting.delete(link.svlan);
    let state = 'up';
    if (link.teardown_error && !inFlight.has(link.svlan)) {
      state = 'failed';
      disconnecting.delete(link.svlan);  // shown again by the API: no need to keep it
    } else if (disconnecting.has(link.svlan) || inFlight.has(link.svlan)) {
      state = 'disconnecting';
    }
    return linkEdge(link.svlan, link.ports, state, state === 'failed' ? link.teardown_error : null);
  });

  // Links being disconnected, which the API no longer lists
  const pendingPorts = topology.ports.filter(p => teardownPending(p) && !shownSvlans.has(p.svlan));
  const pendingSvlans = new Set(pendingPorts.map(p => p.svlan));
  const extraNodes = [];
  for (const [svlan, kept] of [...disconnecting]) {
    if (shownSvlans.has(svlan)) continue;
    // Done once no port of ours holds the SVLAN any more
    if (!topology.ports.some(p => p.svlan === svlan) && !inFlight.has(svlan)) {
      disconnecting.delete(svlan);
      continue;
    }
    pendingSvlans.delete(svlan);
    for (const node of kept.nodes) {
      const known = node.data.type === 'port' ? portsById.has(node.data.portId) : switchesById.has(node.data.switchId);
      if (!known && !extraNodes.some(n => n.data.id === node.data.id)) extraNodes.push(node);
    }
    links.push({ data: { ...kept.edge.data, state: 'disconnecting' } });
  }
  // Asked by someone else, or before a reload: drawn when both ends are known
  for (const svlan of pendingSvlans) {
    const ends = pendingPorts.filter(p => p.svlan === svlan).map(p => p.id);
    if (ends.length === 2) links.push(linkEdge(svlan, ends, 'disconnecting'));
  }

  if (connecting && portsById.has(connecting.portA) && portsById.has(connecting.portB)) {
    links.push({
      data: {
        id: connectingEdgeId(connecting.portA, connecting.portB),
        type: 'link',
        svlan: null,
        source: portNodeId(connecting.portA),
        target: portNodeId(connecting.portB),
        state: 'connecting',
      },
    });
  }

  return {
    switches: [...switches, ...extraNodes.filter(n => n.data.type === 'switch')],
    ports: [...ports, ...extraNodes.filter(n => n.data.type === 'port')],
    links,
  };
}

// --- Tidy layout: Switches on a grid, each Switch's ports in rows along its edge ---

export const PORT_GAP = 56;      // between port centres in a row
export const PORT_ROW_GAP = 48;  // between rows, room for the port names
export const PORTS_PER_ROW = 8;
const SWITCH_GAP_X = 140;
const SWITCH_GAP_Y = 120;        // room for the Switch label above each box

/**
 * Where each port sits relative to its Switch's position (the centre of its ports).
 * @param {number} count - how many ports the Switch has
 * @returns {{x: number, y: number}[]} offsets, in port order
 */
export function portOffsets(count) {
  const cols = Math.min(count, PORTS_PER_ROW);
  const rows = Math.ceil(count / PORTS_PER_ROW);
  return Array.from({ length: count }, (_, i) => {
    const row = Math.floor(i / PORTS_PER_ROW);
    const col = i % PORTS_PER_ROW;
    return { x: (col - (cols - 1) / 2) * PORT_GAP, y: (row - (rows - 1) / 2) * PORT_ROW_GAP };
  });
}

export function switchSize(portCount) {
  const cols = Math.max(Math.min(portCount, PORTS_PER_ROW), 2);
  const rows = Math.max(Math.ceil(portCount / PORTS_PER_ROW), 1);
  return { width: cols * PORT_GAP, height: rows * PORT_ROW_GAP };
}

/**
 * Switch positions for the tidy layout: a grid, in the order given.
 * @param {{ id: number, portCount: number }[]} switches
 * @returns {{ [switchId]: {x: number, y: number} }}
 */
export function tidySwitchPositions(switches) {
  if (!switches.length) return {};
  const columns = Math.ceil(Math.sqrt(switches.length));
  const sizes = switches.map(sw => switchSize(sw.portCount));
  const cellWidth = Math.max(...sizes.map(s => s.width)) + SWITCH_GAP_X;
  const cellHeight = Math.max(...sizes.map(s => s.height)) + SWITCH_GAP_Y;
  return Object.fromEntries(switches.map((sw, i) => [
    sw.id,
    { x: (i % columns) * cellWidth, y: Math.floor(i / columns) * cellHeight },
  ]));
}

// --- What may be done to a selected element: null when allowed, else why not, worded for
// the button's tooltip and the side panel ---

const LOOK_ONLY = 'You may only look at this Topology.';

/** @param {object} port - a port's element data */
export function whyNoConnect(port, { mayWork }) {
  if (!mayWork) return LOOK_ONLY;
  if (!port.inTopology) return 'This port is on a Switch outside this Topology.';
  if (port.teardownPending) return "This port's Link is being disconnected.";
  if (port.svlan != null) return 'This port already has a Link: disconnect it first.';
  return null;
}

/** @param {object} link - a Link's element data */
export function whyNoDisconnect(link, { mayWork }) {
  if (!mayWork) return LOOK_ONLY;
  if (link.state === 'connecting') return 'This Link is being connected.';
  if (link.state === 'disconnecting') return 'This Link is being disconnected.';
  return null;
}

/** @param {object} sw - a Switch's element data */
export function whyNoRelease(sw, { mayWork }) {
  if (!mayWork) return LOOK_ONLY;
  if (!sw.inTopology) return 'This Switch is outside this Topology.';
  return null;
}
