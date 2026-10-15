<template>
  <!-- A new Link: a port on each Switch, the first free one preselected. Either side may be a
       Tool with a free port, drawn or not, but not both. Dropping a drag from
       one Switch onto another opens it with both Switches fixed (`fixed`); the side panel's
       New Link opens it with both to choose. Either way the page connects through the same
       code (Topology.vue connect). -->
  <form class="space-y-3 text-sm" aria-label="New Link" @submit.prevent="submit">
    <fieldset v-for="side in sides" :key="side.key" class="space-y-1.5">
      <legend class="text-xs font-medium uppercase tracking-wide text-gray-500">{{ side.legend }}</legend>
      <p v-if="fixed" class="font-medium text-gray-900">{{ side.switchData?.name }}</p>
      <div v-else>
        <label :for="`${idPrefix}-${side.key}-switch`" class="sr-only">{{ side.legend }} Switch</label>
        <select :id="`${idPrefix}-${side.key}-switch`" v-model="side.model.switchId" class="input w-full" @change="pickFirstPort(side.key)">
          <option v-for="sw in candidates" :key="sw.switchId" :value="sw.switchId">{{ sw.name }}</option>
        </select>
      </div>
      <div v-if="side.freePorts.length">
        <label :for="`${idPrefix}-${side.key}-port`" class="sr-only">{{ side.legend }} port</label>
        <select :id="`${idPrefix}-${side.key}-port`" v-model="side.model.portId" class="input w-full">
          <option v-for="port in side.freePorts" :key="port.portId" :value="port.portId">{{ port.label }}</option>
        </select>
      </div>
    </fieldset>
    <p v-if="whyNot" class="text-xs text-gray-600" data-why-not>{{ whyNot }}</p>
    <div class="flex gap-2">
      <UiButton type="submit" variant="primary" size="sm" :disabled="!!whyNot" :pending="pending">Connect</UiButton>
      <UiButton size="sm" @click="$emit('cancel')">Cancel</UiButton>
    </div>
  </form>
</template>

<script setup>
import { computed, reactive, watch } from 'vue';
import UiButton from '../ui/UiButton.vue';
import { whyNoConnect, whyNoLinkBetween, whyNoLinkOn } from '../../topology/model.js';

const props = defineProps({
  switches: { type: Array, required: true },  // every Switch drawn and every Tool, as element data
  ports: { type: Array, required: true },     // every port, as data (model.js buildElements)
  mayWork: Boolean,
  from: { type: Number, default: null },      // the Switch ids to start with
  to: { type: Number, default: null },
  fixed: Boolean,                             // both Switches fixed: only the ports are chosen
  pending: Boolean,
  idPrefix: { type: String, default: 'new-link' },
});
const emit = defineEmits(['connect', 'cancel']);

const may = computed(() => ({ mayWork: props.mayWork }));
// The Switches a Link may start or end on
const candidates = computed(() => props.switches.filter(sw => !whyNoLinkOn(sw, props.ports, may.value)));
const switchById = (id) => props.switches.find(sw => sw.switchId === id) || null;

const a = reactive({ switchId: null, portId: null });
const b = reactive({ switchId: null, portId: null });

// Free ports of a Switch; on the second side, without the port the first side took
function freePorts(model, other = null) {
  return props.ports.filter(p => p.switchId === model.switchId && !whyNoConnect(p, may.value)
    && (!other || p.portId !== other.portId));
}

function pickFirstPort(key) {
  const side = key === 'a' ? a : b;
  const ports = key === 'a' ? freePorts(a) : freePorts(b, a);
  side.portId = ports[0]?.portId ?? null;
  if (key === 'a' && b.switchId === a.switchId) pickFirstPort('b');
}

// Starts again whenever it is opened on other Switches
watch(() => [props.from, props.to], () => {
  const ids = candidates.value.map(sw => sw.switchId);
  a.switchId = props.fixed || ids.includes(props.from) ? props.from : ids[0] ?? null;
  // The other side: the Switch asked for, else another Switch than the first, else the same
  b.switchId = props.fixed || ids.includes(props.to) ? props.to : (ids.find(id => id !== a.switchId) ?? a.switchId);
  pickFirstPort('a');
  pickFirstPort('b');
}, { immediate: true });

// The free ports change on every refresh: a chosen Switch that can no longer take a Link, or a
// chosen port that isn't free any more, is replaced
watch(() => props.ports, () => {
  if (!props.fixed) {
    const ids = candidates.value.map(sw => sw.switchId);
    if (!ids.includes(a.switchId)) a.switchId = ids[0] ?? null;
    if (!ids.includes(b.switchId)) b.switchId = ids.find(id => id !== a.switchId) ?? a.switchId;
  }
  if (!freePorts(a).some(p => p.portId === a.portId)) pickFirstPort('a');
  if (!freePorts(b, a).some(p => p.portId === b.portId)) pickFirstPort('b');
});

const sides = computed(() => [
  { key: 'a', legend: 'From', model: a, switchData: switchById(a.switchId), freePorts: freePorts(a) },
  { key: 'b', legend: 'To', model: b, switchData: switchById(b.switchId), freePorts: freePorts(b, a) },
]);

// Why Connect is disabled, if it is
const whyNot = computed(() => {
  const swA = switchById(a.switchId);
  const swB = switchById(b.switchId);
  if (!swA || !swB) return candidates.value.length ? 'Choose two Switches.' : 'No Switch of this Topology has a free port.';
  const why = whyNoLinkOn(swA, props.ports, may.value) || whyNoLinkOn(swB, props.ports, may.value)
    || whyNoLinkBetween(swA, swB);
  if (why) return why;
  if (a.switchId === b.switchId && !b.portId) return `${swA.name} has no second free port.`;
  if (!a.portId || !b.portId) return 'Choose a port on each Switch.';
  return null;
});

function submit() {
  if (whyNot.value) return;
  const port = (id) => props.ports.find(p => p.portId === id);
  emit('connect', { a: port(a.portId), b: port(b.portId) });
}
</script>
