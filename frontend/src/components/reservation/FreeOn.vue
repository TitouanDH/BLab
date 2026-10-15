<template>
  <!-- When a Switch can be reserved: now, the end of its Reservation ("17 Oct in 3 days"), or
       why it can't be (Quarantine, Out of service, Cleanup); for a Reservation the user may work
       on, its Renewals left -->
  <div data-testid="free-on" :title="title">
    <template v-if="reservation">
      <span class="tabular-nums text-gray-800">{{ reservation.end_date ? formatDay(reservation.end_date) : 'No end date' }}</span>
      <span v-if="reservation.end_date" class="ml-1 text-xs text-gray-500">{{ relativeDay(new Date(reservation.end_date)) }}</span>
      <div v-if="reservation.admin_exception" class="text-xs text-gray-500">Admin exception: an admin set this end date.</div>
      <div v-else-if="reservation.may_work" class="text-xs text-gray-500">{{ plural(reservation.renewals_left, 'Renewal', 'Renewals') }} left</div>
    </template>
    <span v-else-if="!item.unavailable" class="font-medium text-success-700">Now</span>
    <!-- Why it can't be reserved; an admin may also take a held Switch out of service -->
    <div v-if="item.unavailable" class="text-xs text-warning-800">{{ item.unavailable.reason }}</div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { formatDate, formatDay, relativeDay } from '../../utils/dateUtils.js';
import { plural } from '../../utils/plural.js';

const props = defineProps({
  item: { type: Object, required: true },  // a Reservation page row
});

const reservation = computed(() => props.item.reservation);
// The full end date, with the year and time
const title = computed(() => (reservation.value?.end_date ? formatDate(reservation.value.end_date) : undefined));
</script>
