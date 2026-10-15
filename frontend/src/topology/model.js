// What the Topology canvas draws, worked out from the API's answer: the state of each Switch
// and Link, the cytoscape elements, and the tidy layout. No cytoscape and no Vue here, so the
// rules stay in one place: the cards (cards.js), the canvas style (style.js) and the legend
// (TopologyLegend.vue) are all built from the state tables below.
//
// Every state is a colour *plus* a pattern, a line style or a word, so it reads without colour
// vision. Cytoscape and the cards need colour values: they come from colors.js.
import { switchLabel } from '../utils/switchLabel.js';
import { CARD_HEIGHT, CARD_WIDTH, cardImage, fractionLeft, timeLeft } from './cards.js';
import { COLORS } from './colors.js';

export { COLORS };
const GRAY_700 = COLORS.gray[700];
const GRAY_400 = COLORS.gray[400];
const PRIMARY_600 = COLORS.primary[600];
const PRIMARY_700 = COLORS.primary[700];
const WARNING_600 = COLORS.warning[600];
const WARNING_700 = COLORS.warning[700];
const GHOST_600 = COLORS.ghost[600];

export const ENDING_SOON_MS = 2 * 24 * 60 * 60 * 1000;

// Switch states, in the legend's order. Each is a colour, a stripe pattern, a glyph and a word
// on its card (cards.js). chip: the word on the card; label: the legend's; badge and tone: how
// the side panel names it, in a UiBadge. The rest is the card's look.
export const SWITCH_STATES = {
  topology: { chip: 'MINE', label: 'In this Topology: yours to work on', badge: 'In this Topology', tone: 'neutral',
    stripe: PRIMARY_600, pattern: 'solid', glyph: 'check', bg: '#ffffff', ink: COLORS.gray[900], sub: COLORS.gray[600],
    border: COLORS.gray[300], dash: null, chipInk: PRIMARY_700, muted: false },
  ending: { chip: 'ENDS SOON', label: 'Reservation ends in under 2 days', badge: 'Reservation ends in under 2 days', tone: 'warning',
    stripe: COLORS.warning[500], pattern: 'hatch', glyph: 'clock', bg: '#ffffff', ink: COLORS.gray[900], sub: COLORS.gray[600],
    border: COLORS.warning[400], dash: null, chipInk: WARNING_700, muted: false },
  quarantine: { chip: 'QUARANTINE', label: 'In Quarantine', badge: 'In Quarantine', tone: 'strong-warning',
    stripe: WARNING_700, pattern: 'dots', glyph: 'warning', bg: COLORS.warning[50], ink: COLORS.gray[900], sub: COLORS.warning[900],
    border: WARNING_600, dash: null, chipInk: COLORS.warning[800], muted: false },
  // A Tool (CONTEXT.md), such as the IXIA: drawn once a Link of the Topology reaches it
  tool: { chip: 'TOOL', label: "A Tool: never reserved, its free ports take a Link from any Project", badge: 'Tool', tone: 'neutral',
    stripe: GRAY_700, pattern: 'solid', glyph: 'diamond', bg: '#ffffff', ink: COLORS.gray[900], sub: COLORS.gray[600],
    border: GRAY_400, dash: null, chipInk: GRAY_700, muted: false, tool: true },
  outside: { chip: 'OUTSIDE', label: 'Outside this Topology', badge: 'Outside this Topology', tone: 'neutral',
    stripe: GRAY_400, pattern: 'dash', glyph: 'ring', bg: COLORS.gray[50], ink: COLORS.gray[500], sub: COLORS.gray[500],
    border: COLORS.gray[300], dash: '5 4', chipInk: COLORS.gray[500], muted: true },
};

// Link states, in the legend's order. dash: the dash pattern (null for a plain line), used by
// cytoscape's line-dash-pattern and by the legend's SVG stroke-dasharray alike.
// tone: the side panel's UiBadge (none for a plain Link).
export const LINK_STATES = {
  up: { label: 'Link', color: GRAY_700, dash: null, width: 3, tone: null },
  connecting: { label: 'Being connected', color: PRIMARY_600, dash: [10, 6], width: 3, tone: 'primary' },
  disconnecting: { label: 'Being disconnected', color: GRAY_400, dash: [2, 5], width: 3, tone: 'neutral' },
  failed: { label: 'Disconnect failed', color: WARNING_600, dash: [12, 4, 3, 4], width: 4, tone: 'warning' },
  // A Ghost Link (CONTEXT.md): recorded, but a backbone doesn't carry it
  ghost: { label: 'Carries no traffic', color: GHOST_600, dash: [6, 5], width: 4, tone: 'ghost' },
};

// --- Ids: one place for the canvas ids and the API ids behind them ---

export const switchNodeId = (switchId) => `switch_${switchId}`;
export const linkEdgeId = (svlan) => `link_${svlan}`;
export const connectingEdgeId = (portA, portB) => `connecting_${portA}_${portB}`;

// The management IP only comes with the Switches the user may work on (switchLabel.js)
export const switchName = switchLabel;
// How the page names a port: "1/1/2 on OS6860E-24 #1"
export const portFullName = (port, sw) => `${port.port_switch} on ${switchName(sw)}`;

/**
 * The state a Switch is drawn in. A Tool is always one; for a Switch, a Quarantine wins over
 * everything, then a Reservation ending soon (only for a Switch of the Topology: its holder can act on it).
 */
export function switchState(sw, { quarantined = false, now = Date.now() } = {}) {
  if (sw.is_tool) return 'tool';
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
 * What the canvas draws, without positions: a card per Switch, and each Link from Switch to
 * Switch with its port as a label at each end. Ports aren't drawn: they come as data, for the
 * side panel and the New Link form.
 *
 * A Tool a Link reaches is drawn like a far Switch, as a Tool. Every Tool of the lab, drawn or
 * not, comes back in `tools`, and its free ports in `ports`, for the New Link form.
 *
 * @param {object} topology - the API's answer: { switches, ports, links, tools, tool_ports }
 * @param {object} extra
 *   quarantines: Set of Switch ids in Quarantine (from the Lab status)
 *   holders: { [switchId]: username }
 *   connecting: { portA, portB } while a connect request runs, or null
 *   disconnecting: Map svlan -> { edge, nodes }: Links this page asked to disconnect, as they
 *     were drawn. The API hides a Link being disconnected (unless its teardown failed), and
 *     with it the far end of a Link leaving the Topology; this keeps them drawn meanwhile.
 *     Entries no longer needed are deleted from the Map.
 *   inFlight: Set of SVLANs whose disconnect request is still running
 * @returns {{ switches: object[], links: object[], ports: object[], tools: object[] }} switches
 *   and links: cytoscape element definitions ({ data }); ports: each port's data; tools: the data
 *   of every Tool, as a drawn one has it
 */
export function buildElements(topology, {
  quarantines = new Set(), holders = {}, connecting = null, disconnecting = new Map(),
  inFlight = new Set(), now = Date.now(),
} = {}) {
  const switchesById = new Map(topology.switches.map(sw => [sw.id, sw]));
  const toolsById = new Map((topology.tools || []).map(tool => [tool.id, tool]));
  const shownSvlans = new Set(topology.links.map(l => l.svlan));

  const switchData = (sw) => {
    const state = switchState(sw, { quarantined: quarantines.has(sw.id), now });
    const holder = sw.is_tool ? null : holders[sw.id] || null;
    const endDate = sw.reservation?.end_date || null;
    return {
      id: switchNodeId(sw.id),
      type: 'switch',
      switchId: sw.id,
      name: switchName(sw),
      model: sw.model,
      ip: sw.mngt_IP,
      console: sw.console,
      holder,
      inTopology: !!sw.in_topology,
      isTool: !!sw.is_tool,
      endDate,
      renewalsLeft: sw.reservation?.renewals_left ?? null,
      state,
      card: cardImage({
        model: sw.model, id: sw.id, ip: sw.mngt_IP, holder,
        left: sw.in_topology ? timeLeft(endDate, now) : null,
        fraction: sw.in_topology ? fractionLeft(sw.reservation?.creation_date, endDate, now) : null,
        look: SWITCH_STATES[state],
      }),
    };
  };
  const switches = topology.switches.map(sw => ({ data: switchData(sw) }));

  // The Topology's ports, then the free ports of every Tool (a drawn Tool's linked port is in both lists)
  const seen = new Set();
  const ports = [...topology.ports, ...(topology.tool_ports || [])].filter((port) => {
    if (seen.has(port.id) || !(switchesById.has(port.switch) || toolsById.has(port.switch))) return false;
    seen.add(port.id);
    return true;
  }).map((port) => {
    const sw = switchesById.get(port.switch) || toolsById.get(port.switch);
    return {
      type: 'port',
      portId: port.id,
      switchId: sw.id,
      label: port.port_switch,
      fullName: portFullName(port, sw),
      backbone: port.backbone,
      uni: port.port_backbone,
      inTopology: !!sw.in_topology,
      isTool: !!sw.is_tool,
      svlan: port.svlan ?? null,
      teardownPending: teardownPending(port),
    };
  });
  const portsById = new Map(ports.map(p => [p.portId, p]));

  // One end of a Link, as its edge keeps it: enough to name it once the port is gone
  const end = (port) => ({ portId: port.portId, switchId: port.switchId, label: port.label, fullName: port.fullName });

  const linkEdge = (id, svlan, [a, b], state, { teardownError = null, ghostReason = null, ghostSeenAt = null } = {}) => ({
    data: {
      id,
      type: 'link',
      svlan,
      source: switchNodeId(a.switchId),
      target: switchNodeId(b.switchId),
      sourceLabel: a.label,
      targetLabel: b.label,
      ends: [end(a), end(b)],
      state,
      // Set only when a disconnect failed
      ...(teardownError ? { teardownError } : {}),
      // Set only for a Ghost Link: why, one line per backbone, and when the Link worker found it
      ...(ghostReason ? { ghostReason, ghostSeenAt } : {}),
    },
  });

  const links = [];
  for (const link of topology.links) {
    const linkPorts = link.ports.map(id => portsById.get(id));
    if (linkPorts.length !== 2 || linkPorts.some(p => !p)) continue;
    const kept = disconnecting.get(link.svlan);
    const sameEnds = (edge) => edge.data.ends.map(e => e.portId).sort().join() === [...link.ports].sort().join();
    // The SVLAN now carries a new Link: the one asked for is gone
    if (kept && !sameEnds(kept.edge)) disconnecting.delete(link.svlan);
    let state = 'up';
    if (link.teardown_error && !inFlight.has(link.svlan)) {
      state = 'failed';
      disconnecting.delete(link.svlan);  // shown again by the API: no need to keep it
    } else if (disconnecting.has(link.svlan) || inFlight.has(link.svlan)) {
      state = 'disconnecting';
    } else if (link.ghost_reason) {
      state = 'ghost';
    }
    links.push(linkEdge(linkEdgeId(link.svlan), link.svlan, linkPorts, state, {
      teardownError: state === 'failed' ? link.teardown_error : null,
      ghostReason: state === 'ghost' ? link.ghost_reason : null,
      ghostSeenAt: state === 'ghost' ? link.ghost_seen_at : null,
    }));
  }

  // Links being disconnected, which the API no longer lists
  const pendingPorts = ports.filter(p => p.teardownPending && !shownSvlans.has(p.svlan));
  const pendingSvlans = new Set(pendingPorts.map(p => p.svlan));
  const extraSwitches = [];
  for (const [svlan, kept] of [...disconnecting]) {
    if (shownSvlans.has(svlan)) continue;
    // Done once no port of ours holds the SVLAN any more
    if (!ports.some(p => p.svlan === svlan) && !inFlight.has(svlan)) {
      disconnecting.delete(svlan);
      continue;
    }
    pendingSvlans.delete(svlan);
    for (const node of kept.nodes) {
      if (!switchesById.has(node.data.switchId) && !extraSwitches.some(n => n.data.id === node.data.id)) extraSwitches.push(node);
    }
    links.push({ data: { ...kept.edge.data, state: 'disconnecting' } });
  }
  // Asked by someone else, or before a reload: drawn when both ends are known
  for (const svlan of pendingSvlans) {
    const ends = pendingPorts.filter(p => p.svlan === svlan);
    if (ends.length === 2) links.push(linkEdge(linkEdgeId(svlan), svlan, ends, 'disconnecting'));
  }

  if (connecting && portsById.has(connecting.portA) && portsById.has(connecting.portB)) {
    links.push(linkEdge(connectingEdgeId(connecting.portA, connecting.portB), null,
      [portsById.get(connecting.portA), portsById.get(connecting.portB)], 'connecting'));
  }

  return { switches: [...switches, ...extraSwitches], links, ports, tools: [...toolsById.values()].map(switchData) };
}

// --- Tidy layout: the cards on a grid ---

const SWITCH_GAP_X = 160;  // room for the port labels of a Link between two neighbours
const SWITCH_GAP_Y = 110;

/** A card's size on the canvas */
export const SWITCH_SIZE = { width: CARD_WIDTH, height: CARD_HEIGHT };

/**
 * Switch positions for the tidy layout: a grid, in the order given.
 * @param {string[]} ids
 * @returns {{ [id]: {x: number, y: number} }}
 */
export function tidySwitchPositions(ids) {
  if (!ids.length) return {};
  const columns = Math.ceil(Math.sqrt(ids.length));
  const cellWidth = CARD_WIDTH + SWITCH_GAP_X;
  const cellHeight = CARD_HEIGHT + SWITCH_GAP_Y;
  return Object.fromEntries(ids.map((id, i) => [
    id,
    { x: (i % columns) * cellWidth, y: Math.floor(i / columns) * cellHeight },
  ]));
}

// --- What may be done: null when allowed, else why not, worded for the side panel ---

const LOOK_ONLY = 'You may only look at this Topology.';

/** @param {object} port - a port's data (buildElements ports) */
export function whyNoConnect(port, { mayWork }) {
  if (!mayWork) return LOOK_ONLY;
  // A Tool belongs to no Topology: any Project may take its free ports
  if (!port.inTopology && !port.isTool) return 'This port is on a Switch outside this Topology.';
  if (port.teardownPending) return "This port's Link is being disconnected.";
  if (port.svlan != null) return 'This port already has a Link: disconnect it first.';
  return null;
}

/**
 * Why a Link can't start or end on this Switch, if it can't
 * @param {object} sw - a Switch's element data
 * @param {object[]} ports - every port's data (buildElements ports)
 */
export function whyNoLinkOn(sw, ports, { mayWork }) {
  if (!mayWork) return LOOK_ONLY;
  if (!sw.inTopology && !sw.isTool) return `${sw.name} is outside this Topology: Links stay within it.`;
  if (!ports.some(p => p.switchId === sw.switchId && !whyNoConnect(p, { mayWork }))) return `${sw.name} has no free port.`;
  return null;
}

/** Why a Link can't join these two Switches, if it can't: a Tool only links to a Switch */
export const whyNoLinkBetween = (a, b) => (a.isTool && b.isTool ? 'A Link joins a Tool to a Switch, not to another Tool.' : null);

/** @param {object} link - a Link's element data */
export function whyNoDisconnect(link, { mayWork }) {
  if (!mayWork) return LOOK_ONLY;
  if (link.state === 'connecting') return 'This Link is being connected.';
  if (link.state === 'disconnecting') return 'This Link is being disconnected.';
  return null;
}

/** @param {object} sw - a Switch's element data */
export function whyNoRelease(sw, { mayWork }) {
  if (sw.isTool) return 'A Tool is never reserved: its Link ends with the Release of the Switch at its other end.';
  if (!mayWork) return LOOK_ONLY;
  if (!sw.inTopology) return 'This Switch is outside this Topology.';
  return null;
}
