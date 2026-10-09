<template>
  <!-- One Switch on the Reservation page: its one state, what it means, and the one action
       that state allows (Reserve, Release), or why there is none -->
  <article
    class="flex flex-col rounded-lg bg-white p-4 shadow-sm ring-1"
    :class="state.key === 'mine' ? 'ring-2 ring-primary-600' : 'ring-gray-200'"
    :data-switch="item.mngt_IP"
    :data-state="state.key"
  >
    <div class="flex items-start justify-between gap-2">
      <div class="min-w-0">
        <h3 class="truncate text-lg font-semibold text-gray-900">{{ item.model }}</h3>
        <p class="text-sm text-gray-600">{{ item.mngt_IP }}</p>
      </div>
      <UiBadge :tone="state.tone" class="shrink-0" data-testid="state">
        <UiSpinner v-if="state.key === 'cleaning_up'" size="sm" />
        {{ state.label }}
      </UiBadge>
    </div>

    <div class="mt-3 flex-1 space-y-1 text-sm text-gray-600">
      <template v-if="reservation">
        <p>Until {{ reservation.end_date ? formatDate(reservation.end_date, { relative: true }) : 'no end date' }}</p>
        <p v-if="reservation.admin_exception">Admin exception: an admin set this end date.</p>
        <p v-else-if="reservation.may_work">
          {{ reservation.renewals_left }} Renewal{{ reservation.renewals_left === 1 ? '' : 's' }} left
        </p>
        <p v-if="state.key === 'reserved' && reservation.may_work">
          {{ reservation.username }} shares their Topology with you: you may Renew or Release it.
        </p>
        <p v-else-if="state.key === 'reserved'">
          It can be reserved once {{ reservation.username }} Releases it or the Reservation ends.
        </p>
        <!-- An admin may take a held Switch out of service; a Quarantine may outlive a Release -->
        <p v-if="item.unavailable" class="text-warning-800">{{ item.unavailable.reason }}</p>
      </template>
      <template v-else-if="state.key === 'cleaning_up'">
        <p>BLab is reloading the Switch, then Inspects it. It can be reserved once it is clean.</p>
      </template>
      <template v-else-if="state.key === 'quarantine'">
        <p>{{ item.unavailable.reason }}</p>
        <p>
          It can't be reserved until it is clean.
          <router-link to="/status" class="whitespace-nowrap text-primary-700 hover:underline">Re-check on Lab status</router-link>
        </p>
      </template>
      <template v-else-if="item.unavailable">
        <p>{{ item.unavailable.reason }}</p>
        <p v-if="state.key === 'out_of_service'">Only an admin puts it back in service.</p>
      </template>
    </div>

    <details class="mt-2 text-sm text-gray-600">
      <summary class="cursor-pointer text-primary-700 hover:underline">Details</summary>
      <dl class="mt-1 grid grid-cols-[auto,1fr] gap-x-2">
        <dt class="text-gray-500">Console</dt><dd class="break-all">{{ item.console }}</dd>
        <dt class="text-gray-500">Part number</dt><dd>{{ item.part_number }}</dd>
        <dt class="text-gray-500">Hardware revision</dt><dd>{{ item.hardware_revision }}</dd>
        <dt class="text-gray-500">Serial number</dt><dd>{{ item.serial_number }}</dd>
      </dl>
    </details>

    <div v-if="state.key === 'free' || reservation?.may_work" class="mt-4 flex flex-wrap gap-2">
      <UiButton v-if="state.key === 'free'" variant="primary" @click="$emit('reserve', item.id)">Reserve</UiButton>
      <template v-else>
        <UiButton variant="danger" :disabled="renewing" @click="$emit('release', item.id)">Release</UiButton>
        <UiButton
          v-if="!reservation.admin_exception"
          :pending="renewing"
          :disabled="!reservation.renewals_left"
          :title="reservation.renewals_left ? `Push the end date back by ${RENEWAL_DAYS} days` : 'No Renewals left'"
          @click="$emit('renew', item.id)"
        >
          Renew
        </UiButton>
      </template>
    </div>
  </article>
</template>

<script setup>
// item: a Switch from list_switch (with `unavailable`), plus `reservation`: its row from
// list_reservation (username, may_work, end_date, renewals_left, admin_exception) or null,
// and `mine`: whether the current user holds it.
import { computed } from 'vue';
import UiBadge from './ui/UiBadge.vue';
import UiButton from './ui/UiButton.vue';
import UiSpinner from './ui/UiSpinner.vue';
import { formatDate } from '../utils/dateUtils.js';
import { RENEWAL_DAYS } from '../utils/constants.js';
import { switchState } from '../utils/switchState.js';

const props = defineProps({
  item: { type: Object, required: true },
  renewing: Boolean,
});

defineEmits(['reserve', 'release', 'renew']);

const reservation = computed(() => props.item.reservation);
const state = computed(() => switchState({
  holder: reservation.value?.username,
  mine: props.item.mine,
  unavailable: props.item.unavailable?.state,
}));
</script>
