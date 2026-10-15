<template>
  <!-- One Switch as a tile on the Reservation page's cards: a stripe and badge for its one
       state, when it frees up, its cabled ports, and Reserve + ⋯ -->
  <article
    :class="['flex flex-col rounded-md border-l-4 p-2.5 text-sm shadow-sm',
             STRIPE[state.key] || 'border-gray-300', state.key === 'mine' ? 'bg-primary-50 ring-2 ring-primary-600' : 'bg-white ring-1 ring-gray-200']"
    :data-switch="item.id"
    :data-state="state.key"
  >
    <div class="flex items-start justify-between gap-1">
      <div class="min-w-0">
        <h3 class="truncate font-semibold leading-tight text-gray-900" :title="item.model">{{ item.model }}</h3>
        <p class="truncate text-xs tabular-nums text-gray-500">#{{ item.id }}<template v-if="item.mngt_IP"> · {{ item.mngt_IP }}</template></p>
      </div>
      <UiBadge :tone="state.tone" class="shrink-0" data-testid="state">
        <UiSpinner v-if="state.key === 'cleaning_up'" size="sm" />
        {{ state.label }}
      </UiBadge>
    </div>

    <div class="mt-2 flex items-end justify-between gap-2">
      <div class="min-w-0">
        <div class="text-[10px] font-semibold uppercase tracking-wide text-gray-400">{{ reservation?.may_work ? 'Until' : 'Free on' }}</div>
        <FreeOn :item="item" />
      </div>
      <div class="shrink-0 text-right">
        <div class="text-[10px] font-semibold uppercase tracking-wide text-gray-400">Cabled</div>
        <CabledPorts :count="item.cabled_ports ?? null" bare />
      </div>
    </div>

    <!-- Only for a Switch the user may work on, or for an admin: the API leaves them out otherwise -->
    <details v-if="item.mngt_IP" class="mt-2 text-xs text-gray-600">
      <summary class="cursor-pointer text-primary-700 hover:underline">Details</summary>
      <dl class="mt-1 grid grid-cols-[auto,1fr] gap-x-2">
        <dt class="text-gray-500">Console</dt><dd class="break-all">{{ item.console }}</dd>
        <dt class="text-gray-500">Part number</dt><dd>{{ item.part_number }}</dd>
        <dt class="text-gray-500">Hardware revision</dt><dd>{{ item.hardware_revision }}</dd>
        <dt class="text-gray-500">Serial number</dt><dd>{{ item.serial_number }}</dd>
      </dl>
    </details>

    <div class="mt-auto flex justify-end pt-2">
      <SwitchActions
        :item="item" :state="state.key" :renewing="renewing" :quick-reserving="quickReserving" :projects="projects"
        @reserve="$emit('reserve', $event)" @reserve-until="$emit('reserve-until', $event)" @reserve-into="$emit('reserve-into', $event)"
        @release="$emit('release', $event)" @renew="$emit('renew', $event)" @move="$emit('move', $event)"
      />
    </div>
  </article>
</template>

<script setup>
// item: a Switch from list_switch (with `unavailable`), plus `reservation`: its row from
// list_reservation (username, may_work, end_date, renewals_left, admin_exception) or null,
// and `mine`: whether the current user holds it.
import { computed } from 'vue';
import UiBadge from './ui/UiBadge.vue';
import UiSpinner from './ui/UiSpinner.vue';
import CabledPorts from './reservation/CabledPorts.vue';
import FreeOn from './reservation/FreeOn.vue';
import SwitchActions from './reservation/SwitchActions.vue';
import { switchState } from '../utils/switchState.js';

const props = defineProps({
  item: { type: Object, required: true },
  renewing: Boolean,
  quickReserving: { type: Number, default: null },  // the Switch whose one-click Reserve is under way
  projects: { type: Object, default: null },  // as SwitchActions takes it
});

defineEmits(['reserve', 'reserve-until', 'reserve-into', 'release', 'renew', 'move']);

const reservation = computed(() => props.item.reservation);
const state = computed(() => switchState({
  holder: reservation.value?.username,
  mine: props.item.mine,
  unavailable: props.item.unavailable?.state,
}));

// The state's stripe, in the colours of its badge
const STRIPE = {
  free: 'border-success-500',
  mine: 'border-primary-600',
  reserved: 'border-gray-300',
  cleaning_up: 'border-primary-300',
  quarantine: 'border-warning-500',
  out_of_service: 'border-gray-700',
};
</script>
