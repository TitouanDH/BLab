<template>
  <div class="flex flex-col justify-between rounded-lg bg-white p-4 shadow-sm ring-1 ring-gray-200" :data-switch="item.mngt_IP">
    <div>
      <p class="text-lg font-semibold text-gray-900">{{ item.model }}</p>
      <p class="text-sm text-gray-600">{{ item.mngt_IP }}</p>
      <p class="text-sm text-gray-600">{{ item.console }}</p>
      <button type="button" class="mt-2 text-sm text-primary-700 hover:underline" @click="handleToggleDetails">
        {{ expandedItemId === item.id ? 'Hide details' : 'Details' }}
      </button>
      <div v-show="expandedItemId === item.id" class="mt-2 text-sm text-gray-600">
        <p>Part number: {{ item.part_number }}</p>
        <p>Hardware revision: {{ item.hardware_revision }}</p>
        <p>Serial number: {{ item.serial_number }}</p>
      </div>
    </div>
    <div class="mt-4 space-y-2">
      <!-- Release for the holder, Renew for whoever may work on the Topology; Reserve when not
           reserved; otherwise the state, and why -->
      <div v-if="item.reserved && item.mayRenew" class="flex flex-wrap gap-2">
        <UiButton v-if="item.isOwner" variant="danger" :disabled="isLoading" @click="releaseSwitch">
          Release
        </UiButton>
        <UiButton
          v-if="!item.admin_exception"
          variant="primary"
          :disabled="isLoading || !item.renewals_left"
          :title="item.renewals_left ? `Push the end date back by ${RENEWAL_DAYS} days` : 'No Renewals left'"
          @click="renewReservation"
        >
          Renew ({{ item.renewals_left || 0 }} left)
        </UiButton>
      </div>
      <UiBadge v-else-if="!item.reserved && item.unavailable" :tone="unavailableStates[item.unavailable.state]?.tone || 'neutral'">
        {{ unavailableStates[item.unavailable.state]?.label || 'Unavailable' }}
      </UiBadge>
      <UiButton v-else-if="!item.reserved" variant="primary" :disabled="isLoading" @click="reserveSwitch">
        Reserve
      </UiButton>
      <UiBadge v-else>Reserved</UiBadge>

      <p v-if="!item.reserved && item.unavailable" class="text-sm text-gray-600">
        {{ item.unavailable.reason }}
      </p>
      <div v-if="item.reserved" class="text-sm text-gray-600">
        <p>Reserved by {{ Array.isArray(item.reservedBy) ? item.reservedBy.join(', ') : item.reservedBy }}</p>
        <p v-if="item.end_date">Until {{ formatDate(item.end_date, { relative: true }) }}</p>
        <p v-else>No end date</p>
        <UiBadge v-if="item.admin_exception" class="mt-1">Admin exception: an admin set this end date</UiBadge>
      </div>
    </div>
  </div>
</template>

<script setup>
import UiBadge from './ui/UiBadge.vue';
import UiButton from './ui/UiButton.vue';
import { formatDate } from '../utils/dateUtils.js';
import { RENEWAL_DAYS } from '../utils/constants.js';

const props = defineProps({
  item: Object,
  isLoading: Boolean,
  expandedItemId: Number
});

const emit = defineEmits(['toggleDetails', 'reserve', 'release', 'renew']);

// Why a Switch that nobody holds can't be reserved (see CONTEXT.md)
const unavailableStates = {
  quarantine: { label: 'Quarantine', tone: 'strong-warning' },
  out_of_service: { label: 'Out of service', tone: 'dark' },
  cleaning_up: { label: 'Being Cleaned up', tone: 'primary' },
};

const handleToggleDetails = () => {
  emit('toggleDetails', props.item.id);
};

const reserveSwitch = () => {
  emit('reserve', props.item.id);
};

const renewReservation = () => {
  emit('renew', props.item.id);
};

const releaseSwitch = () => {
  emit('release', props.item.id);
};
</script>
