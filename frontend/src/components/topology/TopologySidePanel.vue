<template>
  <!-- Shown only while something is selected: a Switch with its ports, a Link, or the New Link
       form. A disabled button says why underneath. -->
  <aside aria-label="Selection" class="relative w-80 shrink-0 overflow-y-auto border-l border-gray-200 bg-white p-4 text-sm">
    <button
      type="button"
      class="absolute right-2 top-2 rounded p-1 text-gray-500 hover:bg-gray-100 hover:text-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-600"
      aria-label="Close"
      @click="$emit('close')"
    >
      <XMarkIcon class="h-5 w-5" aria-hidden="true" />
    </button>

    <!-- New Link -->
    <template v-if="newLink">
      <h2 class="pr-8 text-base font-semibold text-gray-900">New Link</h2>
      <p class="mt-1 text-gray-600">Or drag from the handle on a Switch's card to another Switch.</p>
      <NewLinkForm
        class="mt-3"
        id-prefix="panel-link"
        :switches="switches"
        :ports="ports"
        :may-work="mayWork"
        :from="newLink.from"
        :to="newLink.to"
        :pending="connecting"
        @connect="(link) => $emit('connect', link)"
        @cancel="$emit('close')"
      />
    </template>

    <!-- Switch -->
    <template v-else-if="item?.type === 'switch'">
      <div class="flex flex-wrap items-center gap-2 pr-8">
        <h2 class="text-base font-semibold text-gray-900">{{ item.name }}</h2>
        <UiBadge :tone="SWITCH_STATES[item.state].tone">{{ SWITCH_STATES[item.state].badge }}</UiBadge>
      </div>
      <dl class="mt-3 space-y-2">
        <!-- Only for a Switch the user may work on, or for an admin: the API leaves them out otherwise -->
        <template v-if="item.ip">
          <Detail term="Management IP">{{ item.ip }}</Detail>
          <Detail term="Console">{{ item.console || 'Not recorded' }}</Detail>
        </template>
        <Detail v-if="item.isTool" term="Tool">Never reserved. Each free port takes a Link from any Project; the Link ends when the Switch at its other end is Released.</Detail>
        <Detail v-else term="Holder">{{ item.holder || (item.inTopology ? '' : 'Not reserved') }}</Detail>
        <Detail v-if="item.endDate" term="Reservation end">{{ formatDate(item.endDate, { relative: true }) }}</Detail>
        <Detail v-if="item.renewalsLeft !== null" term="Renewals left">{{ item.renewalsLeft }}</Detail>
      </dl>
      <div v-if="quarantine" class="mt-3 rounded-md border border-warning-300 bg-warning-50 p-3 text-warning-800">
        <p class="font-medium">In Quarantine<template v-if="quarantine.holder">, naming {{ quarantine.holder }}</template></p>
        <p v-if="quarantine.reasons?.length" class="mt-1">{{ quarantine.reasons.join('. ') }}.</p>
      </div>

      <h3 class="mt-4 text-xs font-medium uppercase tracking-wide text-gray-500">Links</h3>
      <ul v-if="wired.length" class="mt-1 space-y-1" data-testid="switch-links">
        <li v-for="w in wired" :key="w.port.portId">
          <button
            v-if="w.link"
            type="button"
            class="w-full rounded px-1 py-0.5 text-left hover:bg-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-600"
            @click="$emit('select', w.link.id)"
          >
            <span class="font-mono">{{ w.port.label }}</span> to {{ w.otherEnd.fullName }}
            <span v-if="w.link.state !== 'up'" class="text-gray-500">({{ LINK_STATES[w.link.state].label.toLowerCase() }})</span>
          </button>
          <span v-else class="px-1"><span class="font-mono">{{ w.port.label }}</span>: SVLAN {{ w.port.svlan }}</span>
        </li>
      </ul>
      <p v-else class="mt-1 text-gray-600">No Link.</p>

      <h3 class="mt-4 text-xs font-medium uppercase tracking-wide text-gray-500">Ports without a Link</h3>
      <ul v-if="unwired.length" class="mt-1 space-y-0.5" data-testid="unwired-ports">
        <li v-for="port in unwired" :key="port.portId" class="px-1">
          <span class="font-mono">{{ port.label }}</span>
          <span class="text-gray-500">: UNI {{ port.uni }} on backbone {{ port.backbone }}</span>
        </li>
      </ul>
      <p v-else class="mt-1 text-gray-600">None.</p>

      <Action :why-not="whyNewLink">
        <UiButton variant="primary" :disabled="!!whyNewLink" @click="$emit('new-link', item.switchId)">New Link</UiButton>
      </Action>
      <Action v-if="!item.isTool" :why-not="whyNoRelease(item, { mayWork })">
        <UiButton variant="danger" :disabled="!!whyNoRelease(item, { mayWork })" @click="$emit('release', item)">Release</UiButton>
      </Action>
    </template>

    <!-- Link -->
    <template v-else-if="item?.type === 'link'">
      <div class="flex flex-wrap items-center gap-2 pr-8">
        <h2 class="text-base font-semibold text-gray-900">Link</h2>
        <UiBadge v-if="LINK_STATES[item.state].tone" :tone="LINK_STATES[item.state].tone">{{ LINK_STATES[item.state].label }}</UiBadge>
      </div>
      <dl class="mt-3 space-y-2">
        <Detail v-for="(end, i) in item.ends" :key="end.portId" :term="`End ${i + 1}`">{{ end.fullName }}</Detail>
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
</template>

<script setup>
import { computed, h } from 'vue';
import { XMarkIcon } from '@heroicons/vue/24/outline';
import NewLinkForm from './NewLinkForm.vue';
import UiBadge from '../ui/UiBadge.vue';
import UiButton from '../ui/UiButton.vue';
import { LINK_STATES, SWITCH_STATES, whyNoDisconnect, whyNoLinkOn, whyNoRelease } from '../../topology/model.js';
import { formatDate } from '../../utils/dateUtils.js';
import { plainMessage } from '../../utils/errorHandler.js';

const props = defineProps({
  item: { type: Object, default: null },        // the selected element's data (model.js)
  newLink: { type: Object, default: null },     // { from, to }: the New Link form is open
  switches: { type: Array, default: () => [] }, // every Switch drawn and every Tool, as data: for the New Link form
  links: { type: Array, default: () => [] },    // every Link drawn, as data
  ports: { type: Array, default: () => [] },    // every port, as data (model.js buildElements)
  quarantine: { type: Object, default: null },  // for a Switch: its Quarantine (Lab status)
  mayWork: Boolean,
  busy: Boolean,                                // a disconnect request is running
  connecting: Boolean,                          // a connect request is running
});
defineEmits(['close', 'connect', 'disconnect', 'new-link', 'release', 'select']);

// The selected Switch's ports: with a Link (and the Link drawn on it, if any), and without
const switchPorts = computed(() => (props.item?.type === 'switch'
  ? props.ports.filter(p => p.switchId === props.item.switchId) : []));
const wired = computed(() => switchPorts.value.filter(p => p.svlan != null).map((port) => {
  const link = props.links.find(l => l.ends?.some(e => e.portId === port.portId)) || null;
  return { port, link, otherEnd: link?.ends.find(e => e.portId !== port.portId) || link?.ends[0] };
}));
const unwired = computed(() => switchPorts.value.filter(p => p.svlan == null));
const whyNewLink = computed(() => whyNoLinkOn(props.item, props.ports, { mayWork: props.mayWork }));

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
