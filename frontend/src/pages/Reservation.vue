<template>
  <div class="container mx-auto px-4 py-8">
    <div class="mb-6">
      <h1 class="text-2xl font-bold text-gray-900">Reservation</h1>
      <p class="text-sm text-gray-600">
        Reserve a free Switch for {{ DEFAULT_RESERVATION_DAYS }} days in one click; ⋯ to reserve until a day you choose (up to
        {{ MAX_RESERVATION_DAYS }} days), Renew or Release. Your Switch accounts are on <router-link to="/" class="text-primary-700 hover:underline">My lab</router-link>.
      </p>
    </div>

    <div class="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center">
      <input v-model="searchText" type="search" placeholder="Search Switches" aria-label="Search Switches" class="input lg:w-72" />
      <UiSegmented v-model="filter" label="Show" :options="filters" />
      <!-- With Projects on, every Reservation is made into one -->
      <ProjectChoice v-if="projectsOn" v-model="projectChoice" :projects="activeProjects" class="lg:ml-auto" />
      <UiSegmented v-model="view" label="View" :options="VIEWS" :class="projectsOn ? '' : 'lg:ml-auto'" />
    </div>

    <div v-if="!loaded" class="flex justify-center py-16 text-primary-700"><UiSpinner size="lg" /></div>
    <div v-else class="flex items-start gap-4">
      <!-- The models and a minimum of cabled ports narrow the list -->
      <aside class="sticky top-2 hidden w-60 shrink-0 rounded-lg bg-white p-3 shadow-sm ring-1 ring-gray-200 md:block" aria-label="Models and cabled ports">
        <ModelFacets v-model:picked="pickedModels" v-model:min-ports="minPorts" :families="allFamilies" :max-ports="maxPorts" />
      </aside>

      <div class="min-w-0 flex-1">
        <details class="mb-3 rounded-lg bg-white p-3 shadow-sm ring-1 ring-gray-200 md:hidden">
          <summary class="cursor-pointer text-sm font-semibold text-gray-700">Models and cabled ports</summary>
          <ModelFacets
            v-model:picked="pickedModels" v-model:min-ports="minPorts" id-prefix="phone-"
            :families="allFamilies" :max-ports="maxPorts" class="mt-3"
          />
        </details>

        <p v-if="!rows.length" class="rounded-lg bg-white px-4 py-8 text-center text-gray-600 shadow-sm ring-1 ring-gray-200">
          {{ emptyMessage }}
          <button v-if="narrowed" type="button" class="ml-1 text-primary-700 hover:underline" @click="clearFacets">Show every model</button>
        </p>

        <!-- The table: one compact row per Switch -->
        <div v-else-if="view === 'table'" class="rounded-lg bg-white shadow-sm ring-1 ring-gray-200">
          <div class="flex items-center justify-between gap-3 border-b border-gray-100 px-3 py-2 text-xs text-gray-500">
            <span data-testid="row-count">{{ plural(rows.length, 'Switch', 'Switches') }}<template v-if="narrowed"> · {{ facetSummary }}</template></span>
            <button v-if="narrowed" type="button" class="text-primary-700 hover:underline" @click="clearFacets">Clear</button>
          </div>
          <!-- Scrolls sideways only on narrow screens: it would clip the last rows' ⋯ menus -->
          <div class="overflow-x-auto lg:overflow-visible">
            <table class="min-w-full text-left text-sm">
              <thead class="sticky top-0 z-10 bg-gray-50 text-xs font-semibold text-gray-600 shadow-[0_1px_0_0_theme(colors.gray.200)]">
                <tr>
                  <th
                    v-for="col in COLUMNS" :key="col.key" scope="col" class="px-3 py-2"
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
                        :class="['h-4 w-4', sort.key === col.key ? 'text-primary-700' : 'text-gray-300 group-hover:text-gray-500']"
                      />
                    </button>
                    <span v-else class="sr-only">{{ col.label }}</span>
                  </th>
                </tr>
              </thead>
              <tbody class="divide-y divide-gray-100">
                <tr
                  v-for="s in rows" :key="s.id" :data-switch="s.id" :data-state="stateOf(s).key"
                  :class="['align-top', s.mine ? 'bg-primary-50 shadow-[inset_4px_0_0_theme(colors.primary.600)]' : 'hover:bg-gray-50']"
                >
                  <td class="whitespace-nowrap px-3 py-1.5">
                    <span class="font-medium text-gray-900">{{ s.model }}</span>
                    <span v-if="s.mngt_IP" class="ml-2 text-xs tabular-nums text-gray-500">{{ s.mngt_IP }}</span>
                  </td>
                  <td class="whitespace-nowrap px-3 py-1.5">
                    <UiBadge :tone="stateOf(s).tone" data-testid="state">
                      <UiSpinner v-if="stateOf(s).key === 'cleaning_up'" size="sm" />
                      {{ stateOf(s).label }}
                    </UiBadge>
                  </td>
                  <td class="max-w-sm px-3 py-1.5"><FreeOn :item="s" /></td>
                  <td class="whitespace-nowrap px-3 py-1.5"><CabledPorts :count="s.cabled_ports ?? null" /></td>
                  <td class="whitespace-nowrap px-3 py-1 text-right">
                    <SwitchActions
                      :item="s" :state="stateOf(s).key" :renewing="renewingId === s.id" :quick-reserving="quickReserving" keep-slot
                      :projects="reserveTargets"
                      @reserve="quickReserve" @reserve-until="openReserve" @reserve-into="reserveInto" @release="switchToRelease = $event"
                      @renew="renew" @move="openMove"
                    />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- The cards: tiles under each model family, with how many of the family are free -->
        <div v-else class="space-y-5">
          <section v-for="family in families" :key="family.name" :data-family="family.name" :aria-label="family.name">
            <h2 class="mb-2 flex items-baseline gap-3">
              <span class="text-lg font-semibold text-gray-900">{{ family.name }}</span>
              <span class="text-sm" :class="family.free ? 'font-medium text-success-700' : 'text-gray-500'" data-testid="free-count">
                {{ family.free }} free of {{ family.total }}
              </span>
            </h2>
            <div class="grid grid-cols-1 gap-2 min-[420px]:grid-cols-2 sm:grid-cols-[repeat(auto-fill,minmax(13rem,1fr))]">
              <SwitchCard
                v-for="item in family.switches"
                :key="item.id"
                :item="item"
                :renewing="renewingId === item.id"
                :quick-reserving="quickReserving"
                :projects="reserveTargets"
                @reserve="quickReserve"
                @reserve-into="reserveInto"
                @move="openMove"
                @reserve-until="openReserve"
                @release="switchToRelease = $event"
                @renew="renew"
              />
            </div>
          </section>
        </div>
      </div>
    </div>

    <!-- Reserve until…: choose the end date -->
    <UiModal v-if="reserveId !== null" :title="`Reserve ${reserveName}`" @close="closeReserve">
      <p class="mb-4 text-gray-600">Choose when your Reservation ends (up to {{ MAX_RESERVATION_DAYS }} days from now).</p>
      <label for="end-date" class="mb-2 block font-medium text-gray-700">End date</label>
      <input id="end-date" v-model="endDay" type="date" :min="minDate" :max="isAdmin() ? null : maxDate" class="input" />
      <ProjectChoice v-if="projectsOn" id="reserve-until-project" v-model="projectChoice" :projects="activeProjects" class="mt-4" />
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
    <MoveDialog
      v-if="switchToMove" :switch-id="switchToMove.id" :label="switchToMove.label" :from-project="switchToMove.from"
      @moved="refresh" @close="switchToMove = null"
    />
  </div>
</template>

<script setup>
// The Reservation page: a sidebar of model families
// and models (with how many are free) and a minimum of cabled ports, next to a compact table,
// one row per Switch with its one state (see switchState.js), when it frees up, its cabled
// ports, and Reserve + ⋯. Cards are an option (the choice is remembered). The list is polled
// every 2 s; each answer replaces the whole list at once, so nothing flickers.
import { computed, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import SwitchCard from '../components/SwitchCard.vue';
import CabledPorts from '../components/reservation/CabledPorts.vue';
import FreeOn from '../components/reservation/FreeOn.vue';
import ModelFacets from '../components/reservation/ModelFacets.vue';
import SwitchActions from '../components/reservation/SwitchActions.vue';
import ProjectChoice from '../components/ProjectChoice.vue';
import ReleaseDialog from '../components/ReleaseDialog.vue';
import MoveDialog from '../components/MoveDialog.vue';
import UiBadge from '../components/ui/UiBadge.vue';
import UiButton from '../components/ui/UiButton.vue';
import UiModal from '../components/ui/UiModal.vue';
import UiSegmented from '../components/ui/UiSegmented.vue';
import UiSpinner from '../components/ui/UiSpinner.vue';
import { switchService, reservationService } from '../utils/apiService.js';
import { formatDate, getDefaultReservationDate, getMinReservationDate, getMaxReservationDate, reservationEnd } from '../utils/dateUtils.js';
import { DEFAULT_RESERVATION_DAYS, MAX_RENEWALS, MAX_RESERVATION_DAYS, RENEWAL_DAYS } from '../utils/constants.js';
import { switchState } from '../utils/switchState.js';
import { familyOf, switchLabel } from '../utils/switchLabel.js';
import { groupByFamily } from '../utils/families.js';
import { plural } from '../utils/plural.js';
import { isAdmin, isMe } from '../auth.js';
import { toast } from '../composables/toast.js';
import { isUnreachable, usePoll } from '../composables/poll.js';
import { loadProjects } from '../utils/projects.js';
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

// --- The Project to reserve into, with Projects on: the last used one first ---
const projectsOn = ref(false);
const activeProjects = ref([]);
const projectChoice = ref({ choice: '', name: '', reference: '' });

// Keeps the user's choice unless it is gone (archived, left); `prefer` is a Project just reserved into
const loadChoices = async (prefer = null) => {
  const answer = await loadProjects();
  if (answer.enabled === null) return;
  projectsOn.value = answer.enabled;
  activeProjects.value = answer.active;
  const ids = answer.active.map(p => String(p.id));
  const current = projectChoice.value.choice;
  if (prefer !== null && ids.includes(String(prefer))) projectChoice.value.choice = String(prefer);
  else if (current === 'new' || ids.includes(current)) return;
  else projectChoice.value.choice = answer.lastUsed !== null && ids.includes(String(answer.lastUsed))
    ? String(answer.lastUsed) : (ids[0] || '');
};
// A Project's page sends here with ?project=<id> ("Reserve a Switch into this Project")
loadChoices(useRoute().query.project ?? null);

// Where Reserve goes, for the actions to say so, and the other Projects the ⋯ menu reserves into
const reserveTargets = computed(() => {
  if (!projectsOn.value) return null;
  const { choice, name } = projectChoice.value;
  const into = choice === 'new' ? { name: name || 'a new Project' } : activeProjects.value.find(p => String(p.id) === choice) || null;
  return { into, others: activeProjects.value.filter(p => String(p.id) !== choice) };
});

// What the reserve request says of the Project, or null (with a toast) if it can't be sent
const projectPayload = () => {
  if (!projectsOn.value) return {};
  const { choice, name, reference } = projectChoice.value;
  if (choice === 'new') {
    if (!name) {
      toast.error('Name the new Project first.');
      return null;
    }
    return { new_project: { name, reference } };
  }
  return choice ? { project: Number(choice) } : {};
};

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

// --- The facets: models picked in the sidebar (none: every model), and a minimum of cabled ports ---
const pickedModels = ref([]);
const minPorts = ref(0);
// The slider goes up to the most cabled Switch
const maxPorts = computed(() => Math.max(1, ...switches.value.map(s => s.cabled_ports ?? 0)));
const narrowed = computed(() => pickedModels.value.length > 0 || minPorts.value > 0);
const facetSummary = computed(() => [
  pickedModels.value.length ? [...pickedModels.value].sort().join(', ') : '',
  minPorts.value ? `at least ${plural(minPorts.value, 'cabled port', 'cabled ports')}` : '',
].filter(Boolean).join(', '));
const clearFacets = () => {
  pickedModels.value = [];
  minPorts.value = 0;
};

const emptyMessage = computed(() => {
  if (searchText.value.trim()) return `No Switch matches "${searchText.value.trim()}".`;
  if (narrowed.value) return `No Switch with ${facetSummary.value}${filter.value === 'all' ? '' : ` among ${filter.value === 'free' ? 'the free ones' : 'yours'}`}.`;
  if (filter.value === 'free') return 'No Switch is free right now.';
  if (filter.value === 'mine') return 'You hold no Switch. Reserve a free one.';
  return 'No Switch yet.';
});

// --- Sorting: one column at a time; empty values always last ---
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
  switch: s => [s.model, s.id],
  state: s => STATE_RANK[stateOf(s).key] ?? Object.keys(STATE_RANK).length,
  // Free now, then by the end of the Reservation; a Switch nobody holds that can't be
  // reserved has no date
  freeOn: s => (s.reservation ? time(s.reservation.end_date) : isFree(s) ? 0 : null),
  ports: s => s.cabled_ports ?? null,
};

// Free first, then the soonest free: what helps choose a Switch
const { sort, sortBy, sortIcon, ariaSort, sorted } = useSortableTable(SORT_VALUES, { key: 'freeOn' }, s => [s.model, s.id]);

// The shown Switches, in the table's order
const rows = computed(() => {
  const text = searchText.value.trim().toLowerCase();
  const keeps = FILTERS.find(f => f.key === filter.value).keeps;
  const models = new Set(pickedModels.value);
  return sorted(switches.value.filter(s => keeps(s)
    && (!models.size || models.has(s.model))
    && (s.cabled_ports ?? 0) >= minPorts.value
    && (!text || matches(s, text))));
});

// Every Switch by family and model, with how many are free (whatever the filters): the sidebar
const allFamilies = computed(() => groupByFamily(switches.value, isFree));

// The cards: the shown Switches under their family, in the table's order
const families = computed(() => allFamilies.value
  .map(family => ({ ...family, switches: rows.value.filter(s => familyOf(s.model) === family.name) }))
  .filter(family => family.switches.length));

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
  const project = projectPayload();
  if (project === null) return false;
  const result = await switchService.reserve(switchId, end.toISOString(), project);
  if (!result.success) {
    // The server says why: Quarantine, Out of service, Cleanup in progress...
    toast.error(`Couldn't reserve ${nameOf(switchId)}. ${result.message}`);
    return false;
  }
  const into = result.data?.project ? `, in ${result.data.project.name}` : '';
  toast.success(`${result.data?.detail || 'Reservation successful.'} ${nameOf(switchId)} is yours until ${formatDate(end)}${into}.`);
  if (result.data?.project) {
    projectChoice.value = { choice: String(result.data.project.id), name: '', reference: '' };
    loadChoices(result.data.project.id);
  }
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

// From the ⋯ menu: that Project becomes the choice, then the one-click Reserve
const reserveInto = ({ switchId, projectId }) => {
  if (quickReserving.value !== null) return;
  projectChoice.value = { choice: String(projectId), name: '', reference: '' };
  quickReserve(switchId);
};

// Move a reserved Switch to another of my Projects (MoveDialog)
const switchToMove = ref(null);
const openMove = (switchId) => {
  const s = switches.value.find(x => x.id === switchId);
  switchToMove.value = { id: switchId, label: nameOf(switchId), from: s?.reservation?.project ?? null };
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
