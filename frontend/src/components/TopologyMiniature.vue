<template>
  <!-- A Topology in miniature: its Switches and the Links between them, read-only. The whole
       drawing links to the full canvas. Switches outside the Topology (the far end of a
       Link) are drawn dashed. -->
  <router-link :to="to" class="block rounded-lg bg-white shadow-sm ring-1 ring-gray-200 hover:ring-primary-600" data-testid="topology-miniature">
    <svg :viewBox="`0 0 ${W} ${H}`" class="h-auto w-full" role="img" :aria-label="summary">
      <g v-for="edge in edges" :key="edge.key">
        <path
          :d="edge.path"
          fill="none"
          :class="edge.failed ? 'stroke-warning-600' : 'stroke-primary-700'"
          stroke-width="2"
          :stroke-dasharray="edge.failed ? '6 4' : null"
        />
        <text :x="edge.labelX" :y="edge.labelY" text-anchor="middle" class="fill-gray-600" font-size="11">{{ edge.label }}</text>
      </g>
      <g v-for="node in nodes" :key="node.id">
        <rect
          :x="node.x - NODE_W / 2" :y="node.y - NODE_H / 2" :width="NODE_W" :height="NODE_H" rx="6"
          :class="node.inTopology ? 'fill-primary-50 stroke-primary-700' : 'fill-white stroke-gray-400'"
          stroke-width="1.5"
          :stroke-dasharray="node.inTopology ? null : '4 3'"
        />
        <text :x="node.x" :y="node.y - 3" text-anchor="middle" font-size="12" font-weight="600" class="fill-gray-900">{{ node.model }}</text>
        <text :x="node.x" :y="node.y + 13" text-anchor="middle" font-size="11" class="fill-gray-500">{{ node.ip }}</text>
      </g>
    </svg>
  </router-link>
</template>

<script setup>
import { computed } from 'vue';

const props = defineProps({
  // The topology/<owner_id>/ answer: { switches, ports, links }
  topology: { type: Object, required: true },
  to: { type: [String, Object], default: '/topology' },
});

const W = 640;
const NODE_W = 150;
const NODE_H = 44;

// Two Switches or fewer sit on one line; more go round an ellipse
const H = computed(() => ((props.topology.switches || []).length > 2 ? 240 : 110));

// Switches on an ellipse, starting on the left: two face each other, one sits in the middle
const nodes = computed(() => {
  const switches = props.topology.switches || [];
  const n = switches.length;
  return switches.map((s, i) => {
    const angle = Math.PI + (2 * Math.PI * i) / n;
    const x = n === 1 ? W / 2 : W / 2 + (W / 2 - NODE_W / 2 - 10) * Math.cos(angle);
    const y = n <= 2 ? H.value / 2 : H.value / 2 + (H.value / 2 - NODE_H / 2 - 10) * Math.sin(angle);
    return { id: s.id, model: s.model, ip: s.mngt_IP, inTopology: s.in_topology !== false, x, y };
  });
});

// One line per pair of Switches, labelled with its SVLAN, or with how many Links it stands for
const edges = computed(() => {
  const switchOfPort = new Map((props.topology.ports || []).map(p => [p.id, p.switch]));
  const nodeById = new Map(nodes.value.map(n => [n.id, n]));
  const pairs = new Map();
  for (const link of props.topology.links || []) {
    const ends = link.ports.map(id => switchOfPort.get(id)).filter(id => nodeById.has(id));
    if (ends.length !== 2) continue;
    const [a, b] = [...ends].sort((x, y) => x - y);
    const key = `${a}-${b}`;
    const pair = pairs.get(key) || { key, a, b, svlans: [], failed: false };
    pair.svlans.push(link.svlan);
    pair.failed = pair.failed || !!link.teardown_error;
    pairs.set(key, pair);
  }
  return [...pairs.values()].map(pair => {
    const from = nodeById.get(pair.a);
    const to = nodeById.get(pair.b);
    const label = pair.svlans.length === 1 ? `SVLAN ${pair.svlans[0]}` : `${pair.svlans.length} Links`;
    if (pair.a === pair.b) {
      // Both ends on one Switch: a loop above it
      const top = from.y - NODE_H / 2;
      return { ...pair, label, path: `M ${from.x - 20} ${top} C ${from.x - 40} ${top - 45}, ${from.x + 40} ${top - 45}, ${from.x + 20} ${top}`,
        labelX: from.x, labelY: Math.max(top - 38, 12) };
    }
    return { ...pair, label, path: `M ${from.x} ${from.y} L ${to.x} ${to.y}`,
      labelX: (from.x + to.x) / 2, labelY: (from.y + to.y) / 2 - 6 };
  });
});

const summary = computed(() => {
  const own = nodes.value.filter(n => n.inTopology).length;
  const links = (props.topology.links || []).length;
  return `Topology: ${own} Switch${own === 1 ? '' : 'es'}, ${links} Link${links === 1 ? '' : 's'}. Open the Topology.`;
});
</script>
