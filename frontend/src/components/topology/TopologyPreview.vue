<template>
  <!-- A Topology in small, for the picker: each Switch as a little card with its state stripe,
       where the Topology layout puts it (tidily if none is saved), and its Links. -->
  <svg :viewBox="viewBox" class="h-28 w-full" role="img" :aria-label="summary" preserveAspectRatio="xMidYMid meet">
    <line v-for="(l, i) in lines" :key="i" :x1="l.x1" :y1="l.y1" :x2="l.x2" :y2="l.y2" :stroke="COLORS.gray[500]" stroke-width="6" />
    <g v-for="n in nodes" :key="n.id">
      <rect :x="n.x - W / 2" :y="n.y - H / 2" :width="W" :height="H" rx="14"
        :fill="n.look.bg" :stroke="n.look.border" stroke-width="4" :stroke-dasharray="n.look.dash ? n.look.dash.split(' ').map(d => d * 3).join(' ') : undefined" />
      <rect :x="n.x - W / 2 + 2" :y="n.y - H / 2 + 2" width="14" :height="H - 4" rx="4" :fill="n.look.stripe" />
      <text :x="n.x - W / 2 + 30" :y="n.y + 8" font-size="30" font-weight="700" :fill="n.look.ink">{{ n.model }}</text>
    </g>
  </svg>
</template>

<script setup>
import { computed } from 'vue';
import { COLORS, SWITCH_SIZE, SWITCH_STATES, switchState, tidySwitchPositions } from '../../topology/model.js';
import { plural } from '../../utils/plural.js';

const props = defineProps({
  // The topology/<owner>/ or projects/<id>/topology/ answer: { switches, ports, links, layout }
  topology: { type: Object, required: true },
});

const W = SWITCH_SIZE.width;
const H = SWITCH_SIZE.height;
const PAD = 40;
// At least this much is shown, so one Switch isn't blown up to fill the preview
const MIN_WIDTH = 3 * W + 2 * 160;
const MIN_HEIGHT = 2 * H + 110;

const nodes = computed(() => {
  const switches = props.topology.switches || [];
  const saved = props.topology.layout || {};
  const tidy = tidySwitchPositions(switches.map(sw => String(sw.id)));
  return switches.map(sw => ({
    id: sw.id,
    model: sw.model,
    look: SWITCH_STATES[switchState(sw)],
    ...(saved[sw.id] || tidy[String(sw.id)]),
  }));
});

const lines = computed(() => {
  const switchOfPort = new Map((props.topology.ports || []).map(p => [p.id, p.switch]));
  const at = new Map(nodes.value.map(n => [n.id, n]));
  return (props.topology.links || []).map((link) => {
    const [a, b] = link.ports.map(id => at.get(switchOfPort.get(id)));
    return a && b && a !== b ? { x1: a.x, y1: a.y, x2: b.x, y2: b.y } : null;
  }).filter(Boolean);
});

const viewBox = computed(() => {
  if (!nodes.value.length) return `0 0 ${W} ${H}`;
  const xs = nodes.value.map(n => n.x);
  const ys = nodes.value.map(n => n.y);
  let [x1, x2] = [Math.min(...xs) - W / 2 - PAD, Math.max(...xs) + W / 2 + PAD];
  let [y1, y2] = [Math.min(...ys) - H / 2 - PAD, Math.max(...ys) + H / 2 + PAD];
  const growX = Math.max(0, MIN_WIDTH - (x2 - x1)) / 2;
  const growY = Math.max(0, MIN_HEIGHT - (y2 - y1)) / 2;
  [x1, x2, y1, y2] = [x1 - growX, x2 + growX, y1 - growY, y2 + growY];
  return `${x1} ${y1} ${x2 - x1} ${y2 - y1}`;
});

const summary = computed(() => {
  const own = (props.topology.switches || []).filter(sw => sw.in_topology).length;
  const links = (props.topology.links || []).length;
  return `${plural(own, 'Switch', 'Switches')}, ${plural(links, 'Link', 'Links')}`;
});
</script>
