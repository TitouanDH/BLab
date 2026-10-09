<template>
  <div class="container mx-auto px-4 py-8">
    <div class="mb-6">
      <h1 class="text-2xl font-bold text-gray-900">Reservation</h1>
      <p class="text-sm text-gray-600">
        Reserve a free Switch for up to {{ MAX_RESERVATION_DAYS }} days, Renew it, and Release it when you are done.
      </p>
    </div>

    <div class="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <input v-model="searchText" type="search" placeholder="Search Switches" aria-label="Search Switches" class="input sm:w-1/2" />
      <div class="inline-flex rounded-md shadow-sm" role="group" aria-label="Show">
        <button
          v-for="(f, i) in filters"
          :key="f.key"
          type="button"
          :aria-pressed="filter === f.key"
          :class="[
            'px-3 py-1.5 text-sm font-medium ring-1 ring-inset ring-gray-300 focus:z-10 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-600',
            i === 0 ? 'rounded-l-md' : '-ml-px',
            i === filters.length - 1 ? 'rounded-r-md' : '',
            filter === f.key ? 'bg-primary-700 text-white ring-primary-700' : 'bg-white text-gray-700 hover:bg-gray-50',
          ]"
          @click="filter = f.key"
        >
          {{ f.label }} ({{ f.count }})
        </button>
      </div>
    </div>

    <div v-if="!loaded" class="flex justify-center py-16 text-primary-700"><UiSpinner size="lg" /></div>
    <p v-else-if="!shown.length" class="rounded-lg bg-white px-4 py-8 text-center text-gray-600 shadow-sm ring-1 ring-gray-200">
      {{ emptyMessage }}
    </p>
    <div v-else class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      <SwitchCard
        v-for="item in shown"
        :key="item.id"
        :item="item"
        :renewing="renewingId === item.id"
        @reserve="openReserve"
        @release="switchToRelease = $event"
        @renew="renew"
      />
    </div>

    <!-- Reserve dialog: choose the end date -->
    <UiModal v-if="reserveId !== null" :title="`Reserve ${reserveName}`" @close="closeReserve">
      <p class="mb-4 text-gray-600">Choose when your Reservation ends (up to {{ MAX_RESERVATION_DAYS }} days from now).</p>
      <label for="end-date" class="mb-2 block font-medium text-gray-700">End date</label>
      <input id="end-date" v-model="endDay" type="date" :min="minDate" :max="isAdmin() ? null : maxDate" class="input" />
      <ul class="mt-4 space-y-1 text-xs text-gray-500">
        <li>Default: 7 days from today</li>
        <li>
          Maximum: {{ MAX_RESERVATION_DAYS }} days from now. Renew it to keep it longer: {{ RENEWAL_DAYS }} days at a time,
          {{ MAX_RENEWALS }} times at most.
        </li>
        <li v-if="isAdmin()">As an admin, you may pick any later day: the Reservation is then an admin exception.</li>
      </ul>
      <template #actions>
        <UiButton @click="closeReserve">Cancel</UiButton>
        <UiButton variant="primary" :pending="reserving" @click="confirmReserve">Reserve</UiButton>
      </template>
    </UiModal>
    <ReleaseDialog v-if="switchToRelease !== null" :switchId="switchToRelease" @released="refresh" @close="switchToRelease = null" />
  </div>
</template>

<script setup>
// The Reservation page: every Switch with its one state (see switchState.js). The list is
// polled every 2 s; each answer replaces the whole list at once, so nothing flickers.
import { computed, ref } from 'vue';
import SwitchCard from '../components/SwitchCard.vue';
import ReleaseDialog from '../components/ReleaseDialog.vue';
import UiButton from '../components/ui/UiButton.vue';
import UiModal from '../components/ui/UiModal.vue';
import UiSpinner from '../components/ui/UiSpinner.vue';
import { switchService, reservationService } from '../utils/apiService.js';
import { getDefaultReservationDate, getMinReservationDate, getMaxReservationDate, reservationEnd } from '../utils/dateUtils.js';
import { MAX_RENEWALS, MAX_RESERVATION_DAYS, RENEWAL_DAYS } from '../utils/constants.js';
import { switchState } from '../utils/switchState.js';
import { isAdmin, isMe } from '../auth.js';
import { toast } from '../composables/toast.js';
import { isUnreachable, usePoll } from '../composables/poll.js';

const switches = ref([]);   // list_switch rows, each with its `reservation` (or null) and `mine`
const loaded = ref(false);   // the first answer has come
const searchText = ref('');
const filter = ref('all');

// Both lists in one go, then one assignment: no in-between state reaches the page
const load = async () => {
  const results = await Promise.all([switchService.getAll(), reservationService.getAll()]);
  const [switchList, reservationList] = results;
  if (switchList.success && reservationList.success) {
    const bySwitch = new Map(reservationList.data.map(r => [r.switch, r]));
    switches.value = switchList.data.switchs.map(s => {
      const reservation = bySwitch.get(s.id) || null;
      return { ...s, reservation, mine: !!reservation && isMe(reservation.user) };
    });
    loaded.value = true;
  } else {
    const refused = results.find(r => !r.success && !isUnreachable(r));
    if (refused) toast.error(`Couldn't load the Switches. ${refused.message}`, 'reservation-load');
  }
  return results;
};

const { refresh } = usePoll(load, 2 * 1000);

const isFree = (s) => switchState({ holder: s.reservation?.username, unavailable: s.unavailable?.state }).key === 'free';

// Which Switches each filter keeps
const FILTERS = [
  { key: 'all', label: 'All', keeps: () => true },
  { key: 'free', label: 'Free', keeps: isFree },
  { key: 'mine', label: 'Mine', keeps: s => s.mine },
];

const filters = computed(() => FILTERS.map(f => ({ ...f, count: switches.value.filter(f.keeps).length })));

const matches = (s, text) => [s.model, s.mngt_IP, s.console, s.part_number, s.hardware_revision, s.serial_number,
  s.reservation?.username].some(field => (field || '').toLowerCase().includes(text));

const shown = computed(() => {
  const text = searchText.value.trim().toLowerCase();
  const keeps = FILTERS.find(f => f.key === filter.value).keeps;
  return switches.value.filter(s => keeps(s) && (!text || matches(s, text)));
});

const emptyMessage = computed(() => {
  if (searchText.value.trim()) return `No Switch matches "${searchText.value.trim()}".`;
  if (filter.value === 'free') return 'No Switch is free right now.';
  if (filter.value === 'mine') return 'You hold no Switch. Reserve a free one.';
  return 'No Switch yet.';
});

// --- Reserve ---
const reserveId = ref(null);
const endDay = ref('');
const reserving = ref(false);
const minDate = getMinReservationDate();
const maxDate = getMaxReservationDate();

const reserveName = computed(() => {
  const s = switches.value.find(x => x.id === reserveId.value);
  return s ? `${s.model} (${s.mngt_IP})` : 'this Switch';
});

const openReserve = (switchId) => {
  reserveId.value = switchId;
  endDay.value = getDefaultReservationDate();
};

const closeReserve = () => {
  if (!reserving.value) reserveId.value = null;
};

const confirmReserve = async () => {
  // The end of the chosen day, within the limit the server enforces
  const end = endDay.value ? reservationEnd(endDay.value) : null;
  if (!end || isNaN(end.getTime())) {
    toast.error('Choose a valid end date.');
    return;
  }
  if (end < new Date()) {
    toast.error('The end date must be in the future.');
    return;
  }
  reserving.value = true;
  const result = await switchService.reserve(reserveId.value, end.toISOString());
  reserving.value = false;
  if (result.success) {
    toast.success(result.data?.detail || 'Reservation successful.');
    closeReserve();
    refresh();
  } else {
    // The server says why: Quarantine, Out of service, Cleanup in progress...
    toast.error(`Couldn't reserve this Switch. ${result.message}`);
  }
};

// --- Renewal: pushes the end date back, a limited number of times (the server decides) ---
const renewingId = ref(null);
const renew = async (switchId) => {
  if (renewingId.value !== null) return;
  renewingId.value = switchId;
  const result = await switchService.renew(switchId);
  if (result.success) {
    toast.success(result.data.detail);
  } else {
    toast.error(`Couldn't Renew this Reservation. ${result.message}`);
  }
  await refresh();
  renewingId.value = null;
};

// --- Release: the dialog confirms it; the card then shows the Cleanup until it is done ---
const switchToRelease = ref(null);
</script>
