<template>
  <!-- The selected Switch, port or Link: its details and what can be done to it. A disabled
       button says why underneath. The legend sits at the bottom. -->
  <div class="flex w-80 shrink-0 flex-col border-l border-gray-200 bg-white">
    <aside aria-label="Selection" class="min-h-0 flex-1 overflow-y-auto p-4 text-sm">
      <template v-if="!item">
        <h2 class="text-base font-semibold text-gray-900">Nothing selected</h2>
        <p class="mt-2 text-gray-600">Click a Switch, a port or a Link to see its details and what you can do with it.</p>
        <p class="mt-2 text-gray-600">To make a Link, click a port, press <span class="font-semibold">Connect</span>, then click another port.</p>
      </template>

      <!-- Switch -->
      <template v-else-if="item.type === 'switch'">
        <div class="flex flex-wrap items-center gap-2">
          <h2 class="text-base font-semibold text-gray-900">{{ item.model }}</h2>
          <UiBadge :tone="SWITCH_STATES[item.state].tone">{{ SWITCH_STATES[item.state].badge }}</UiBadge>
        </div>
        <dl class="mt-3 space-y-2">
          <Detail term="Management IP">{{ item.ip }}</Detail>
          <Detail term="Console">{{ item.console || 'Not recorded' }}</Detail>
          <Detail term="Holder">{{ item.holder || (item.inTopology ? '' : 'Not reserved') }}</Detail>
          <Detail v-if="item.endDate" term="Reservation end">{{ formatDate(item.endDate, { relative: true }) }}</Detail>
          <Detail v-if="item.renewalsLeft !== null" term="Renewals left">{{ item.renewalsLeft }}</Detail>
        </dl>
        <div v-if="quarantine" class="mt-3 rounded-md border border-warning-300 bg-warning-50 p-3 text-warning-800">
          <p class="font-medium">In Quarantine<template v-if="quarantine.holder">, naming {{ quarantine.holder }}</template></p>
          <p v-if="quarantine.reasons?.length" class="mt-1">{{ quarantine.reasons.join('. ') }}.</p>
        </div>
        <Action :why-not="whyNoRelease(item, { mayWork })">
          <UiButton variant="danger" :disabled="!!whyNoRelease(item, { mayWork })" @click="$emit('release', item)">Release</UiButton>
        </Action>
      </template>

      <!-- Port -->
      <template v-else-if="item.type === 'port'">
        <h2 class="text-base font-semibold text-gray-900">Port {{ item.label }}</h2>
        <p class="text-gray-600">{{ item.fullName.replace(`${item.label} on `, 'on ') }}</p>
        <dl class="mt-3 space-y-2">
          <Detail term="UNI">{{ item.uni }} on backbone {{ item.backbone }}</Detail>
          <Detail term="Link">
            <template v-if="link && otherEnd">
              To {{ otherEnd.fullName }}, SVLAN {{ link.svlan }}
              <span v-if="link.state !== 'up'" class="text-gray-500">({{ LINK_STATES[link.state].label.toLowerCase() }})</span>
            </template>
            <template v-else-if="item.svlan != null">SVLAN {{ item.svlan }}</template>
            <template v-else>No Link</template>
          </Detail>
        </dl>
        <div class="mt-4 flex flex-wrap gap-2">
          <template v-if="link">
            <UiButton @click="$emit('select', link.id)">Show Link</UiButton>
            <UiButton variant="danger" :disabled="!!whyNoDisconnect(link, { mayWork })" :pending="busy" @click="$emit('disconnect', link)">Disconnect</UiButton>
          </template>
          <UiButton v-else variant="primary" :disabled="!!whyNoConnect(item, { mayWork })" @click="$emit('connect', item)">Connect</UiButton>
        </div>
        <p v-if="whyNot" class="mt-2 text-xs text-gray-500">{{ whyNot }}</p>
      </template>

      <!-- Link -->
      <template v-else-if="item.type === 'link'">
        <div class="flex flex-wrap items-center gap-2">
          <h2 class="text-base font-semibold text-gray-900">Link</h2>
          <UiBadge v-if="LINK_STATES[item.state].tone" :tone="LINK_STATES[item.state].tone">{{ LINK_STATES[item.state].label }}</UiBadge>
        </div>
        <dl class="mt-3 space-y-2">
          <Detail v-for="(end, i) in ends" :key="end.id" :term="`End ${i + 1}`">
            {{ end.label }} on {{ end.fullName.replace(`${end.label} on `, '') }}
          </Detail>
          <Detail v-if="item.svlan" term="SVLAN">{{ item.svlan }}</Detail>
        </dl>
        <div v-if="item.teardownError" class="mt-3 rounded-md border border-warning-300 bg-warning-50 p-3 text-warning-800">
          <p class="font-medium">Disconnecting this Link failed.</p>
          <p class="mt-1">{{ plainMessage(item.teardownError) }}</p>
          <p class="mt-1">Press Disconnect to try again.</p>
        </div>
        <div v-if="item.ghostReason" class="mt-3 rounded-md border border-ghost-300 bg-ghost-50 p-3 text-ghost-800" data-ghost-reason>
          <p class="font-medium">This Link carries no traffic.</p>
          <!-- One line per backbone that doesn't carry it -->
          <p v-for="(line, i) in item.ghostReason.split(/\r?\n/)" :key="i" class="mt-1">{{ line }}</p>
          <p class="mt-1 text-xs">
            Found {{ formatDate(item.ghostSeenAt) }} by BLab's regular check of the backbones.
            Disconnecting and connecting it again builds it anew; if that doesn't help, tell an admin.
          </p>
        </div>
        <Action :why-not="whyNoDisconnect(item, { mayWork })">
          <UiButton variant="danger" :disabled="!!whyNoDisconnect(item, { mayWork })" :pending="busy" @click="$emit('disconnect', item)">Disconnect</UiButton>
        </Action>
      </template>
    </aside>
    <!-- Always in view, below the selection: it never hides part of the canvas -->
    <div class="shrink-0 border-t border-gray-200 p-4">
      <TopologyLegend />
    </div>
  </div>
</template>

<script setup>
import { computed, h } from 'vue';
import TopologyLegend from './TopologyLegend.vue';
import UiBadge from '../ui/UiBadge.vue';
import UiButton from '../ui/UiButton.vue';
import { LINK_STATES, SWITCH_STATES, whyNoConnect, whyNoDisconnect, whyNoRelease } from '../../topology/model.js';
import { formatDate } from '../../utils/dateUtils.js';
import { plainMessage } from '../../utils/errorHandler.js';

const props = defineProps({
  item: { type: Object, default: null },        // the selected element's data (model.js)
  link: { type: Object, default: null },        // for a port: its Link's data, if drawn
  otherEnd: { type: Object, default: null },    // for a port: the far end of its Link
  ends: { type: Array, default: () => [] },     // for a Link: both ends' data
  quarantine: { type: Object, default: null },  // for a Switch: its Quarantine (Lab status)
  mayWork: Boolean,
  busy: Boolean,                                // a disconnect request is running
});
defineEmits(['connect', 'disconnect', 'release', 'select']);

// Why the port's buttons are disabled, if they are
const whyNot = computed(() => (props.link
  ? whyNoDisconnect(props.link, { mayWork: props.mayWork })
  : whyNoConnect(props.item, { mayWork: props.mayWork })));

// One term and its value
const Detail = (p, { slots }) => h('div', [
  h('dt', { class: 'text-xs font-medium uppercase tracking-wide text-gray-500' }, p.term),
  h('dd', { class: 'text-gray-900' }, slots.default?.()),
]);
Detail.props = ['term'];

// A button, and why it is disabled underneath
const Action = (p, { slots }) => h('div', { class: 'mt-4' }, [
  slots.default?.(),
  p.whyNot ? h('p', { class: 'mt-2 text-xs text-gray-500' }, p.whyNot) : null,
]);
Action.props = ['whyNot'];
</script>
