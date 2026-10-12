<template>
  <div class="container mx-auto px-4 py-8">
    <div class="mb-6">
      <h1 class="text-2xl font-bold text-gray-900">Reservation</h1>
      <p class="text-sm text-gray-600">
        Reserve a free Switch for {{ DEFAULT_RESERVATION_DAYS }} days in one click, or until a day you choose (up to
        {{ MAX_RESERVATION_DAYS }} days). Your Switch accounts are on <router-link to="/" class="text-primary-700 hover:underline">My lab</router-link>.
      </p>
    </div>

    <div class="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center">
      <input v-model="searchText" type="search" placeholder="Search Switches" aria-label="Search Switches" class="input lg:w-72" />
      <UiSegmented v-model="filter" label="Show" :options="filters" />
      <UiSegmented v-model="view" label="View" :options="VIEWS" class="lg:ml-auto" />
    </div>

    <div v-if="!loaded" class="flex justify-center py-16 text-primary-700"><UiSpinner size="lg" /></div>
    <p v-else-if="!groups.length" class="rounded-lg bg-white px-4 py-8 text-center text-gray-600 shadow-sm ring-1 ring-gray-200">
      {{ emptyMessage }}
    </p>

    <!-- The table: one group of rows per model, with how many of that model are free -->
    <div v-else-if="view === 'table'" class="overflow-x-auto rounded-lg bg-white shadow-sm ring-1 ring-gray-200">
      <table class="min-w-full text-left text-sm">
        <thead class="bg-gray-50 text-gray-700">
          <tr>
            <th
              v-for="col in COLUMNS" :key="col.key" scope="col"
              :class="['px-4 py-2 font-semibold', col.key === 'ports' ? 'text-right' : '']"
              :aria-sort="col.sorts ? ariaSort(col.key) : undefined"
            >
              <button
                v-if="col.sorts" type="button"
                class="group inline-flex items-center gap-1 rounded hover:text-gray-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-600"
                :title="col.title"
                @click="sortBy(col.key)"
              >
                {{ col.label }}
                <component
                  :is="sortIcon(col.key)" aria-hidden="true"
                  :class="['h-4 w-4', sort.key === col.key ? 'text-primary-700' : 'text-gray-400 group-hover:text-gray-600']"
                />
              </button>
              <span v-else class="sr-only">{{ col.label }}</span>
            </th>
          </tr>
        </thead>
        <tbody v-for="group in groups" :key="group.model" :data-model="group.model">
          <tr class="border-t bg-gray-50/60">
            <th :colspan="COLUMNS.length" scope="rowgroup" class="px-4 py-1.5 font-semibold text-gray-900">
              {{ group.model }}
              <span class="ml-2 text-xs font-medium" :class="group.free ? 'text-success-700' : 'text-gray-500'" data-testid="free-count">
                {{ group.free }} free of {{ group.total }}
              </span>
            </th>
          </tr>
          <tr v-for="s in group.switches" :key="s.id" class="border-t align-top" :data-switch="s.id" :data-state="stateOf(s).key">
            <td class="whitespace-nowrap px-4 py-2">
              <span class="font-medium text-gray-900">#{{ s.id }}</span>
              <span v-if="s.mngt_IP" class="ml-2 text-gray-500">{{ s.mngt_IP }}</span>
            </td>
            <td class="whitespace-nowrap px-4 py-2">
              <UiBadge :tone="stateOf(s).tone" data-testid="state">
                <UiSpinner v-if="stateOf(s).key === 'cleaning_up'" size="sm" />
                {{ stateOf(s).label }}
              </UiBadge>
            </td>
            <td class="max-w-md px-4 py-2 text-gray-700" data-testid="free-on">
              <template v-if="s.reservation">
                <div>{{ s.reservation.end_date ? formatDate(s.reservation.end_date, { relative: true }) : 'No end date' }}</div>
                <div v-if="s.reservation.admin_exception" class="text-xs text-gray-500">Admin exception: an admin set this end date.</div>
                <div v-else-if="s.reservation.may_work" class="text-xs text-gray-500">
                  {{ s.reservation.renewals_left }} Renewal{{ s.reservation.renewals_left === 1 ? '' : 's' }} left
                </div>
              </template>
              <span v-else-if="!s.unavailable" class="font-medium text-success-700">Now</span>
              <!-- Why it can't be reserved; an admin may also take a held Switch out of service -->
              <div v-if="s.unavailable" class="text-xs text-warning-800">{{ s.unavailable.reason }}</div>
            </td>
            <td class="px-4 py-2 text-right tabular-nums" data-testid="cabled-ports">{{ s.cabled_ports ?? '-' }}</td>
            <td class="whitespace-nowrap px-4 py-2 text-right">
              <div class="inline-flex gap-2">
                <template v-if="stateOf(s).key === 'free'">
                  <UiButton size="sm" variant="primary" :pending="quickReserving === s.id" :disabled="quickReserving !== null"
                    :title="`Reserve for ${DEFAULT_RESERVATION_DAYS} days`" @click="quickReserve(s.id)">Reserve</UiButton>
                  <UiButton size="sm" @click="openReserve(s.id)">Reserve until…</UiButton>
                </template>
                <template v-else-if="s.reservation?.may_work">
                  <UiButton size="sm" variant="danger" :disabled="renewingId === s.id" @click="switchToRelease = s.id">Release</UiButton>
                  <UiButton
                    v-if="!s.reservation.admin_exception" size="sm"
                    :pending="renewingId === s.id" :disabled="!s.reservation.renewals_left"
                    :title="s.reservation.renewals_left ? `Push the end date back by ${RENEWAL_DAYS} days` : 'No Renewals left'"
                    @click="renew(s.id)"
                  >
                    Renew
                  </UiButton>
                </template>
                <router-link v-else-if="stateOf(s).key === 'quarantine'" to="/status" class="text-xs font-semibold text-primary-700 hover:underline">
                  Re-check on Lab status
                </router-link>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- The cards: the same groups, in the same order -->
    <div v-else class="space-y-6">
      <section v-for="group in groups" :key="group.model" :data-model="group.model" :aria-label="group.model">
        <h2 class="mb-2 text-lg font-semibold text-gray-900">
          {{ group.model }}
          <span class="ml-2 text-sm font-medium" :class="group.free ? 'text-success-700' : 'text-gray-500'" data-testid="free-count">
            {{ group.free }} free of {{ group.total }}
          </span>
        </h2>
        <div class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          <SwitchCard
            v-for="item in group.switches"
            :key="item.id"
            :item="item"
            :renewing="renewingId === item.id"
            :quick-reserving="quickReserving"
            @reserve="quickReserve"
            @reserve-until="openReserve"
            @release="switchToRelease = $event"
            @renew="renew"
          />
        </div>
      </section>
    </div>

    <!-- Reserve until…: choose the end date -->
    <UiModal v-if="reserveId !== null" :title="`Reserve ${reserveName}`" @close="closeReserve">
      <p class="mb-4 text-gray-600">Choose when your Reservation ends (up to {{ MAX_RESERVATION_DAYS }} days from now).</p>
      <label for="end-date" class="mb-2 block font-medium text-gray-700">End date</label>
      <input id="end-date" v-model="endDay" type="date" :min="minDate" :max="isAdmin() ? null : maxDate" class="input" />
      <ul class="mt-4 space-y-1 text-xs text-gray-500">
        <li>Default: {{ DEFAULT_RESERVATION_DAYS }} days from today</li>
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
// The Reservation page: every Switch with its one state (see switchState.js), grouped by
// model, as a sortable table or as cards (the choice is remembered). The list is polled every
// 2 s; each answer replaces the whole list at once, so nothing flickers.
import { computed, ref, watch } from 'vue';
import SwitchCard from '../components/SwitchCard.vue';
import ReleaseDialog from '../components/ReleaseDialog.vue';
import UiBadge from '../components/ui/UiBadge.vue';
import UiButton from '../components/ui/UiButton.vue';
import UiModal from '../components/ui/UiModal.vue';
import UiSegmented from '../components/ui/UiSegmented.vue';
import UiSpinner from '../components/ui/UiSpinner.vue';
import { switchService, reservationService } from '../utils/apiService.js';
import { formatDate, getDefaultReservationDate, getMinReservationDate, getMaxReservationDate, reservationEnd } from '../utils/dateUtils.js';
import { DEFAULT_RESERVATION_DAYS, MAX_RENEWALS, MAX_RESERVATION_DAYS, RENEWAL_DAYS } from '../utils/constants.js';
import { switchState } from '../utils/switchState.js';
import { switchLabel } from '../utils/switchLabel.js';
import { isAdmin, isMe } from '../auth.js';
import { toast } from '../composables/toast.js';
import { isUnreachable, usePoll } from '../composables/poll.js';
import { time, useSortableTable } from '../composables/sortableTable.js';

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

const stateOf = (s) => switchState({ holder: s.reservation?.username, mine: s.mine, unavailable: s.unavailable?.state });
const isFree = (s) => stateOf(s).key === 'free';

// --- Table or cards, remembered ---
const VIEW_KEY = 'reservation.view';
const VIEWS = [{ key: 'table', label: 'Table' }, { key: 'cards', label: 'Cards' }];
// Until the user chooses: the table, but cards on a phone, where the table would scroll sideways
const narrow = () => window.matchMedia('(max-width: 639px)').matches;
const view = ref(VIEWS.some(v => v.key === localStorage.getItem(VIEW_KEY)) ? localStorage.getItem(VIEW_KEY) : (narrow() ? 'cards' : 'table'));
watch(view, value => localStorage.setItem(VIEW_KEY, value));

// --- Filters and search ---
const FILTERS = [
  { key: 'all', label: 'All', keeps: () => true },
  { key: 'free', label: 'Free', keeps: isFree },
  { key: 'mine', label: 'Mine', keeps: s => s.mine },
];

const filters = computed(() => FILTERS.map(f => ({ key: f.key, label: `${f.label} (${switches.value.filter(f.keeps).length})` })));

const matches = (s, text) => [switchLabel(s), s.mngt_IP, s.console, s.part_number, s.hardware_revision, s.serial_number,
  s.reservation?.username].some(field => (field || '').toLowerCase().includes(text));

const emptyMessage = computed(() => {
  if (searchText.value.trim()) return `No Switch matches "${searchText.value.trim()}".`;
  if (filter.value === 'free') return 'No Switch is free right now.';
  if (filter.value === 'mine') return 'You hold no Switch. Reserve a free one.';
  return 'No Switch yet.';
});

// --- Sorting: one column at a time, within each model; empty values always last ---
const COLUMNS = [
  { key: 'switch', label: 'Switch', sorts: true },
  { key: 'state', label: 'State', sorts: true },
  { key: 'freeOn', label: 'Free on', sorts: true, title: 'When it can be reserved: now, the end of its Reservation, or why not' },
  { key: 'ports', label: 'Cabled ports', sorts: true, title: 'Its ports cabled to a backbone: how many Links it can carry' },
  { key: 'actions', label: 'Actions', sorts: false },
];
// The easiest to get first when ascending
const STATE_RANK = { free: 0, mine: 1, reserved: 2, cleaning_up: 3, quarantine: 4, out_of_service: 5 };

// The value a column sorts on, or null for an empty cell
const SORT_VALUES = {
  switch: s => s.id,
  state: s => STATE_RANK[stateOf(s).key] ?? Object.keys(STATE_RANK).length,
  // Free now, then by the end of the Reservation; a Switch nobody holds that can't be
  // reserved has no date
  freeOn: s => (s.reservation ? time(s.reservation.end_date) : isFree(s) ? 0 : null),
  ports: s => s.cabled_ports ?? null,
};

// Free first, then the soonest free: what helps choose a Switch
const { sort, sortBy, sortIcon, ariaSort, sorted } = useSortableTable(SORT_VALUES, { key: 'freeOn' }, s => s.id);

// The models in alphabetical order, each with its shown Switches and how many of all its
// Switches are free (whatever the filters)
const groups = computed(() => {
  const text = searchText.value.trim().toLowerCase();
  const keeps = FILTERS.find(f => f.key === filter.value).keeps;
  const byModel = new Map();
  for (const s of switches.value) {
    if (!byModel.has(s.model)) byModel.set(s.model, []);
    byModel.get(s.model).push(s);
  }
  return [...byModel.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([model, all]) => ({
      model,
      total: all.length,
      free: all.filter(isFree).length,
      switches: sorted(all.filter(s => keeps(s) && (!text || matches(s, text)))),
    }))
    .filter(g => g.switches.length);
});

// --- Reserve: one click for the default days, or until a chosen day ---
const nameOf = (switchId) => {
  const s = switches.value.find(x => x.id === switchId);
  return s ? switchLabel(s) : 'this Switch';
};

// The end is the end of the chosen day, within the limit the server enforces
const reserve = async (switchId, day) => {
  const end = day ? reservationEnd(day) : null;
  if (!end || isNaN(end.getTime())) {
    toast.error('Choose a valid end date.');
    return false;
  }
  if (end < new Date()) {
    toast.error('The end date must be in the future.');
    return false;
  }
  const result = await switchService.reserve(switchId, end.toISOString());
  if (!result.success) {
    // The server says why: Quarantine, Out of service, Cleanup in progress...
    toast.error(`Couldn't reserve ${nameOf(switchId)}. ${result.message}`);
    return false;
  }
  toast.success(`${result.data?.detail || 'Reservation successful.'} ${nameOf(switchId)} is yours until ${formatDate(end)}.`);
  // The password is shown in "Your Switch accounts" on My lab, never in a toast
  const account = result.data?.switch_account;
  if (account?.state === 'failed') {
    toast.error(`BLab couldn't create your Switch account on ${nameOf(switchId)} yet; it keeps trying. ${account.error || ''}`.trim());
  }
  refresh();
  return true;
};

const quickReserving = ref(null);
const quickReserve = async (switchId) => {
  if (quickReserving.value !== null) return;
  quickReserving.value = switchId;
  await reserve(switchId, getDefaultReservationDate());
  quickReserving.value = null;
};

const reserveId = ref(null);
const endDay = ref('');
const reserving = ref(false);
const minDate = getMinReservationDate();
const maxDate = getMaxReservationDate();
const reserveName = computed(() => nameOf(reserveId.value));

const openReserve = (switchId) => {
  reserveId.value = switchId;
  endDay.value = getDefaultReservationDate();
};

const closeReserve = () => {
  if (!reserving.value) reserveId.value = null;
};

const confirmReserve = async () => {
  reserving.value = true;
  const done = await reserve(reserveId.value, endDay.value);
  reserving.value = false;
  if (done) closeReserve();
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

// --- Release: the dialog confirms it; the row then shows the Cleanup until it is done ---
const switchToRelease = ref(null);
</script>
