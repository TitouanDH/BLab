// The card of a Switch on the Topology canvas (#43, style A): a white card with the
// state as a left stripe, a faceplate of the model family, the model with its id and the IP
// (when the API sends it) on top; the holder, the time left as words and as a bar, and the
// state as a word along the bottom. Drawn as an SVG image that cytoscape paints on the node.
//
// Every state is a colour *and* a pattern and a word (model.js SWITCH_STATES), so it reads
// without colour vision. No cytoscape and no Vue here.
import { COLORS as C, FONT, MONO } from './colors.js';

export const CARD_WIDTH = 236;
export const CARD_HEIGHT = 96;

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

// The stripe's fill: the state's colour, plain or patterned
function stripeDefs(look) {
  const { stripe, pattern } = look;
  if (pattern === 'hatch') {
    return `<pattern id="p" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <rect width="6" height="6" fill="${stripe}"/><rect width="2.5" height="6" fill="#ffffff" opacity="0.55"/></pattern>`;
  }
  if (pattern === 'dots') {
    return `<pattern id="p" width="5" height="5" patternUnits="userSpaceOnUse">
      <rect width="5" height="5" fill="${stripe}"/><circle cx="2.5" cy="2.5" r="1.1" fill="#ffffff" opacity="0.8"/></pattern>`;
  }
  if (pattern === 'dash') {
    return `<pattern id="p" width="8" height="8" patternUnits="userSpaceOnUse">
      <rect width="8" height="5" fill="${stripe}"/><rect y="5" width="8" height="3" fill="#ffffff"/></pattern>`;
  }
  return '';
}
const stripeFill = (look) => (look.pattern === 'solid' ? look.stripe : 'url(#p)');

// A small glyph per state, before its word
function stateGlyph(glyph, x, y, color) {
  if (glyph === 'warning') {
    return `<path d="M${x + 5} ${y} L${x + 10} ${y + 9} L${x} ${y + 9} Z" fill="${color}"/><rect x="${x + 4.4}" y="${y + 2.8}" width="1.2" height="3.4" fill="#fff"/><rect x="${x + 4.4}" y="${y + 6.9}" width="1.2" height="1.2" fill="#fff"/>`;
  }
  if (glyph === 'clock') {
    return `<circle cx="${x + 5}" cy="${y + 4.5}" r="4.3" fill="none" stroke="${color}" stroke-width="1.4"/><path d="M${x + 5} ${y + 2} V${y + 4.8} H${x + 7}" stroke="${color}" stroke-width="1.3" fill="none"/>`;
  }
  if (glyph === 'ring') {
    return `<circle cx="${x + 5}" cy="${y + 4.5}" r="4.3" fill="none" stroke="${color}" stroke-width="1.4" stroke-dasharray="2 1.6"/>`;
  }
  return `<circle cx="${x + 5}" cy="${y + 4.5}" r="4.3" fill="${color}"/><path d="M${x + 2.8} ${y + 4.6} l1.6 1.6 l3 -3.2" stroke="#fff" stroke-width="1.3" fill="none"/>`;
}

// --- Faceplates: one drawing per kind of model family ---

/**
 * Which faceplate a model gets, from its family (the "OS6860" of "OS6860E-24"): data centre,
 * campus, small fanless or hardened. Not to scale, just recognisable.
 */
function faceplateKind(model = '') {
  const family = (String(model).match(/^OS(\d{2})/i) || [])[1];
  if (['69', '99'].includes(family)) return 'datacenter';
  if (['62', '63', '22', '23'].includes(family)) return 'small';
  if (family === '64' || /^OS6865/i.test(model)) return 'hardened';
  return 'campus';
}

/** The faceplate of a model, drawn in a w x h box at (x, y), as SVG */
function faceplate(kind, x, y, w, h, { muted = false } = {}) {
  const body = muted ? C.gray[300] : C.gray[700];
  const port = muted ? C.gray[100] : C.gray[200];
  const accent = muted ? C.gray[400] : C.primary[400];
  const parts = [`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${Math.min(4, h / 6)}" fill="${body}"/>`];
  const grid = (x0, y0, cols, rows, pw, ph, gx, gy, fill = port) => {
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        parts.push(`<rect x="${(x0 + c * (pw + gx)).toFixed(1)}" y="${(y0 + r * (ph + gy)).toFixed(1)}" width="${pw.toFixed(1)}" height="${ph.toFixed(1)}" rx="0.6" fill="${fill}"/>`);
      }
    }
  };
  const pad = h * 0.16;
  const inner = h - 2 * pad;
  if (kind === 'datacenter') {
    // Two rows of small SFP+ cages, then four QSFP
    const ph = (inner - pad * 0.6) / 2;
    const cols = 10;
    const area = w * 0.7;
    const pw = (area - (cols - 1) * 1.6) / cols;
    grid(x + pad, y + pad, cols, 2, pw, ph, 1.6, pad * 0.6);
    const qx = x + pad + area + w * 0.04;
    const qw = (x + w - pad - qx - 3 * 2) / 4;
    grid(qx, y + pad, 4, 1, qw, inner, 2, 0, accent);
  } else if (kind === 'small') {
    // One row of RJ45 and two SFP, a light body
    parts[0] = `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${Math.min(4, h / 6)}" fill="${muted ? C.gray[200] : C.gray[400]}"/>`;
    const pw = (w * 0.72 - 11 * 1.4) / 12;
    grid(x + pad, y + h * 0.3, 12, 1, pw, h * 0.4, 1.4, 0, muted ? '#fff' : C.gray[700]);
    grid(x + w * 0.8, y + h * 0.3, 2, 1, w * 0.07, h * 0.4, 2, 0, accent);
  } else if (kind === 'hardened') {
    // DIN rail: a ribbed body with few ports
    const rib = muted ? C.gray[400] : C.gray[600];
    for (let i = 0; i < 5; i++) parts.push(`<rect x="${(x + w * 0.04 + i * w * 0.045).toFixed(1)}" y="${y + pad}" width="${(w * 0.025).toFixed(1)}" height="${inner}" fill="${rib}"/>`);
    const ph = (inner - pad * 0.5) / 2;
    grid(x + w * 0.3, y + pad, 6, 2, (w * 0.45 - 5 * 1.4) / 6, ph, 1.4, pad * 0.5);
    grid(x + w * 0.8, y + pad, 2, 1, (w * 0.16 - 2) / 2, inner, 2, 0, accent);
  } else {
    // Campus: 2 x 12 RJ45 in two blocks, 4 SFP on the right
    const ph = (inner - pad * 0.5) / 2;
    const pw = (w * 0.32 - 5 * 1.4) / 6;
    grid(x + pad, y + pad, 6, 2, pw, ph, 1.4, pad * 0.5);
    grid(x + pad + w * 0.35, y + pad, 6, 2, pw, ph, 1.4, pad * 0.5);
    const sx = x + w * 0.8;
    grid(sx, y + pad, 2, 2, (x + w - pad - sx - 2) / 2, ph, 2, pad * 0.5, accent);
  }
  // A LED
  parts.push(`<circle cx="${x + w - pad * 0.8}" cy="${y + h - pad * 0.7}" r="${Math.max(1, h * 0.04)}" fill="${muted ? C.gray[400] : C.primary[300]}"/>`);
  return parts.join('');
}

// --- Time left ---

const MINUTE = 60 * 1000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** "6d 3h left", "5h left", "40m left", "Ended"; null without an end date */
export function timeLeft(endDate, now = Date.now()) {
  if (!endDate) return null;
  const left = new Date(endDate).getTime() - now;
  if (left <= 0) return 'Ended';
  const days = Math.floor(left / DAY);
  const hours = Math.floor((left % DAY) / HOUR);
  if (days) return hours ? `${days}d ${hours}h left` : `${days}d left`;
  if (hours) return `${hours}h left`;
  return `${Math.max(1, Math.floor(left / MINUTE))}m left`;
}

/** How much of the Reservation is left, 0 to 1; null without both dates */
export function fractionLeft(startDate, endDate, now = Date.now()) {
  if (!startDate || !endDate) return null;
  const start = new Date(startDate).getTime();
  const end = new Date(endDate).getTime();
  if (end <= start) return 0;
  return Math.min(1, Math.max(0, (end - now) / (end - start)));
}

const svgUri = (w, h, body) =>
  `data:image/svg+xml;utf8,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">${body}</svg>`)}`;

/**
 * A small sample of a state's card, for the legend: its background, border and stripe
 * @param {object} look - model.js SWITCH_STATES[state]
 */
export function swatchImage(look) {
  const body = `<defs>${stripeDefs(look)}</defs>
    <rect x="0.75" y="0.75" width="26.5" height="14.5" rx="3" fill="${look.bg}" stroke="${look.border}" stroke-width="1.5" ${look.dash ? 'stroke-dasharray="3 2"' : ''}/>
    <rect x="0.75" y="0.75" width="6" height="14.5" fill="${stripeFill(look)}"/>`;
  return svgUri(28, 16, body);
}

// --- The card ---

function avatar(x, y, r, name, muted) {
  const initial = name ? esc(name[0].toUpperCase()) : '–';
  return `<circle cx="${x}" cy="${y}" r="${r}" fill="${muted ? C.gray[300] : C.primary[100]}"/><text x="${x}" y="${y + r * 0.38}" text-anchor="middle" font-family="${FONT}" font-size="${r * 1.05}" font-weight="700" fill="${muted ? C.gray[600] : C.primary[800]}">${initial}</text>`;
}

/**
 * The card of one Switch, as an image cytoscape can paint.
 *
 * @param {object} card
 *   model, id: the Switch; ip: its management IP, or null when the API leaves it out (#36)
 *   holder: the holder's username, or null; left: timeLeft(), or null; fraction: fractionLeft()
 *   look: the state's look (model.js SWITCH_STATES[state]): stripe, pattern, bg, ink, sub,
 *     border, dash, chip, glyph, muted
 * @returns {string} a data: URI of an SVG of CARD_WIDTH x CARD_HEIGHT
 */
export function cardImage({ model, id, ip, holder, left, fraction, look }) {
  const W = CARD_WIDTH;
  const H = CARD_HEIGHT;
  const R = 10;
  const fill = stripeFill(look);
  const body = `<defs>${stripeDefs(look)}<clipPath id="c"><rect x="1" y="1" width="${W - 2}" height="${H - 2}" rx="${R}"/></clipPath></defs>
    <rect x="1" y="1" width="${W - 2}" height="${H - 2}" rx="${R}" fill="${look.bg}" stroke="${look.border}" stroke-width="1.5" ${look.dash ? `stroke-dasharray="${look.dash}"` : ''}/>
    <rect x="1" y="1" width="9" height="${H - 2}" fill="${fill}" clip-path="url(#c)"/>
    ${faceplate(faceplateKind(model), 20, 14, 64, 22, { muted: look.muted })}
    <text x="94" y="25" font-family="${FONT}" font-size="14" font-weight="700" fill="${look.ink}">${esc(model)}<tspan font-size="12" font-weight="400" fill="${look.sub}"> #${esc(id)}</tspan></text>
    ${ip
      ? `<text x="94" y="41" font-family="${MONO}" font-size="11.5" fill="${look.sub}">${esc(ip)}</text>`
      : `<text x="94" y="41" font-family="${FONT}" font-size="11" font-style="italic" fill="${C.gray[400]}">IP for members only</text>`}
    <line x1="18" y1="54" x2="${W - 10}" y2="54" stroke="${C.gray[200]}"/>
    ${avatar(30, 72, 9, holder, look.muted)}
    <text x="45" y="70" font-family="${FONT}" font-size="12" font-weight="600" fill="${look.ink}">${esc(holder || 'Not reserved')}</text>
    ${left ? `<text x="45" y="84" font-family="${FONT}" font-size="11" fill="${look.sub}">${esc(left)}</text>` : ''}
    ${fraction !== null && fraction !== undefined ? `<rect x="${W - 96}" y="78" width="84" height="5" rx="2.5" fill="${C.gray[200]}"/>
    <rect x="${W - 96}" y="78" width="${Math.max(4, 84 * fraction).toFixed(1)}" height="5" rx="2.5" fill="${fill}"/>` : ''}
    <g>${stateGlyph(look.glyph, W - 96, 62, look.stripe)}<text x="${W - 82}" y="70" font-family="${FONT}" font-size="10" font-weight="700" letter-spacing="0.5" fill="${look.chipInk || look.stripe}">${esc(look.chip)}</text></g>`;
  return svgUri(W, H, body);
}
