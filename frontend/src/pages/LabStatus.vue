<template>
  <div class="container mx-auto px-4 py-8">
    <div class="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h1 class="text-2xl font-bold text-gray-900">Lab status</h1>
        <p class="text-sm text-gray-600">
          Every Switch, who holds it, what its last Inspection found, and whether it is in Quarantine
          or Out of service. An Inspection only reads the Switch.
        </p>
      </div>
      <UiButton variant="primary" :pending="isLoading" @click="load">Refresh</UiButton>
    </div>

    <!-- The last nightly Sweep's report: only there when it found something wrong, one line
         until opened (remembered) -->
    <section v-if="sweep" aria-label="Last Sweep report" class="mb-4 rounded-lg bg-warning-50 text-sm text-warning-900 ring-1 ring-warning-200">
      <div class="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2">
        <ExclamationTriangleIcon class="h-5 w-5 shrink-0 text-warning-600" aria-hidden="true" />
        <h2 class="font-semibold">
          Last Sweep ({{ formatDate(sweep.finished_at) }}): {{ sweep.problems.length }} problem{{ sweep.problems.length === 1 ? '' : 's' }}
        </h2>
        <button
          type="button" class="ml-auto font-semibold text-warning-900 hover:underline"
          :aria-expanded="sweepOpen" aria-controls="sweep-details" @click="toggleSweep"
        >
          {{ sweepOpen ? 'Hide' : 'Show' }}
        </button>
      </div>
      <div v-show="sweepOpen" id="sweep-details" class="border-t border-warning-200 px-4 py-3">
        <p>{{ sweep.summary }}</p>
        <ul class="mt-2 list-disc space-y-1 pl-5">
          <li v-for="(problem, i) in sweep.problems" :key="i">{{ problem }}</li>
        </ul>
      </div>
    </section>

    <!-- Search, and the counts as filters: a Switch shows if it matches any pressed one -->
    <div class="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center">
      <input v-model="searchText" type="search" placeholder="Search Switch, IP or holder" aria-label="Search Switches" class="input lg:w-72" />
      <div role="group" aria-label="Filter by state" class="flex flex-wrap items-center gap-2">
        <button
          v-for="f in filters" :key="f.key" type="button" :aria-pressed="pressed.includes(f.key)"
          :class="[
            'inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold ring-1 ring-inset focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-600',
            pressed.includes(f.key) ? f.pressedClass : 'bg-white text-gray-700 ring-gray-300 hover:bg-gray-50',
          ]"
          @click="toggleFilter(f.key)"
        >
          <span :class="['h-2 w-2 rounded-full', f.dot]" aria-hidden="true"></span>
          {{ f.count }} {{ f.label }}
        </button>
        <button v-if="pressed.length || searchText" type="button" class="text-xs font-semibold text-primary-700 hover:underline" @click="clearFilters">
          Clear filters
        </button>
      </div>
      <p class="text-sm text-gray-500 lg:ml-auto" data-testid="shown-count">{{ shown.length }} of {{ switches.length }} Switches</p>
    </div>

    <div v-if="!loaded" class="flex justify-center py-16 text-primary-700"><UiSpinner size="lg" /></div>

    <div v-else class="overflow-x-auto rounded-lg bg-white shadow-sm ring-1 ring-gray-200">
      <table class="min-w-full text-left text-sm">
        <thead class="bg-gray-50 text-gray-700">
          <tr>
            <th v-for="col in COLUMNS" :key="col.key" class="px-4 py-3 font-semibold" :aria-sort="col.sorts ? ariaSort(col.key) : undefined">
              <button
                v-if="col.sorts" type="button"
                class="group inline-flex items-center gap-1 rounded hover:text-gray-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-600"
                @click="sortBy(col.key)"
              >
                {{ col.label }}
                <component
                  :is="sortIcon(col.key)" aria-hidden="true"
                  :class="['h-4 w-4', sort.key === col.key ? 'text-primary-700' : 'text-gray-400 group-hover:text-gray-600']"
                />
              </button>
              <span v-else :class="{ 'sr-only': !col.label }">{{ col.label || 'Details' }}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!shown.length">
            <td :colspan="COLUMNS.length" class="px-4 py-8 text-center text-gray-600">
              No Switch matches.
              <button type="button" class="ml-1 font-semibold text-primary-700 hover:underline" @click="clearFilters">Clear filters</button>
            </td>
          </tr>
          <template v-for="s in shown" :key="s.id">
            <tr class="border-t align-top" :data-row="s.id">
              <td class="px-4 py-3">
                <div class="font-medium text-gray-900">{{ s.model }}</div>
                <div class="text-gray-500">#{{ s.id }}<template v-if="s.mngt_IP"> · {{ s.mngt_IP }}</template></div>
              </td>
              <td class="max-w-xs px-4 py-3" :data-switch="s.id">
                <!-- The same badges as the Reservation page (switchState.js); a Switch that nobody
                     holds may show several reasons it can't be reserved, each with its details -->
                <div v-if="s.holder || (!s.out_of_service && !s.quarantine && !s.cleaning_up)" class="mb-2">
                  <UiBadge :tone="mainState(s).tone">{{ mainState(s).label }}</UiBadge>
                </div>
                <div v-if="s.out_of_service">
                  <UiBadge :tone="UNAVAILABLE.out_of_service.tone">{{ UNAVAILABLE.out_of_service.label }}</UiBadge>
                  <div class="mt-1 text-xs text-gray-600">{{ s.out_of_service.reason }}</div>
                </div>
                <div v-if="s.quarantine" :class="{ 'mt-2': s.out_of_service }">
                  <UiBadge :tone="UNAVAILABLE.quarantine.tone">{{ UNAVAILABLE.quarantine.label }}</UiBadge>
                  <div class="mt-1 text-xs text-gray-600">
                    {{ s.quarantine.holder ? `Names ${s.quarantine.holder}` : 'Names nobody: an admin clears it' }},
                    since {{ formatDate(s.quarantine.opened_at) }}
                  </div>
                  <ul class="mt-1 text-xs text-warning-800">
                    <li v-for="reason in s.quarantine.reasons" :key="reason">{{ reason }}</li>
                  </ul>
                  <UiButton v-if="!s.cleaning_up" size="sm" variant="primary" class="mt-2" :pending="rechecking === s.id" @click="recheck(s)">
                    Re-check
                  </UiButton>
                </div>
                <div v-if="s.cleaning_up" :class="{ 'mt-2': s.out_of_service || s.quarantine }">
                  <UiBadge :tone="UNAVAILABLE.cleaning_up.tone"><UiSpinner size="sm" /> {{ UNAVAILABLE.cleaning_up.label }}</UiBadge>
                  <div class="mt-1 text-xs text-gray-600">Reloading, then Inspected</div>
                </div>
              </td>
              <td class="px-4 py-3">{{ s.holder || '-' }}</td>
              <td class="px-4 py-3">
                {{ s.end_date ? formatDate(s.end_date, { relative: true }) : '-' }}
                <div v-if="s.admin_exception" class="mt-1">
                  <UiBadge>Admin exception</UiBadge>
                </div>
                <div v-else-if="s.holder" class="mt-1 text-xs text-gray-500">
                  {{ s.renewals_left }} Renewal{{ s.renewals_left === 1 ? '' : 's' }} left
                </div>
              </td>
              <td class="px-4 py-3">
                <UiBadge :tone="INSPECTION[inspectionState(s)].tone">{{ INSPECTION[inspectionState(s)].label }}</UiBadge>
                <div v-if="s.inspection" class="mt-1 text-xs text-gray-500">{{ formatDate(s.inspection.at) }}</div>
              </td>
              <!-- The first finding, the rest in the details row -->
              <td class="max-w-sm px-4 py-3" data-testid="findings">
                <template v-if="findings(s).length">
                  <span :class="findings(s)[0].reason ? 'text-warning-800' : 'break-all text-gray-600'">{{ findings(s)[0].text }}</span>
                  <button
                    v-if="findings(s).length > 1" type="button"
                    class="ml-1 whitespace-nowrap rounded-full bg-gray-100 px-2 py-0.5 text-xs font-semibold text-gray-700 hover:bg-gray-200"
                    :aria-label="`${findings(s).length - 1} more finding${findings(s).length === 2 ? '' : 's'}`"
                    :aria-expanded="expanded === s.id" :aria-controls="`details-${s.id}`" @click="toggle(s.id)"
                  >
                    +{{ findings(s).length - 1 }}
                  </button>
                </template>
              </td>
              <td class="px-4 py-3 text-right">
                <button
                  v-if="hasDetails(s)" type="button" class="text-primary-700 hover:underline"
                  :aria-expanded="expanded === s.id" :aria-controls="`details-${s.id}`" @click="toggle(s.id)"
                >
                  {{ expanded === s.id ? 'Hide' : 'Details' }}
                </button>
              </td>
            </tr>
            <tr v-if="expanded === s.id" :id="`details-${s.id}`" class="bg-gray-50" data-testid="details">
              <td :colspan="COLUMNS.length" class="px-4 py-3">
                <div class="grid gap-4 md:grid-cols-2">
                  <div v-if="findings(s).length">
                    <h3 class="mb-1 font-semibold text-gray-900">Findings</h3>
                    <ul class="list-disc space-y-1 pl-5" data-testid="all-findings">
                      <li v-for="f in findings(s)" :key="f.text" :class="f.reason ? 'text-warning-800' : 'break-all text-gray-600'">{{ f.text }}</li>
                    </ul>
                  </div>
                  <div v-if="s.history.length">
                    <h3 class="mb-1 font-semibold text-gray-900">History</h3>
                    <ul class="space-y-1">
                      <li v-for="(e, i) in s.history" :key="i">
                        <span class="text-gray-500">{{ formatDate(e.at) }}</span>
                        <span class="ml-2 font-medium">{{ kindLabels[e.kind] || e.kind }}</span>
                        <span v-if="e.user" class="text-gray-500"> ({{ e.user }})</span>:
                        <span :class="e.ok ? 'text-success-700' : 'text-warning-800'">{{ e.reasons.length ? e.reasons.join('; ') : (e.ok ? 'ok' : 'failed') }}</span>
                        <span v-if="e.warnings.length" class="text-gray-600"> ({{ e.warnings.length }} warning(s))</span>
                      </li>
                    </ul>
                  </div>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { ExclamationTriangleIcon } from '@heroicons/vue/20/solid';
import UiBadge from '../components/ui/UiBadge.vue';
import UiButton from '../components/ui/UiButton.vue';
import UiSpinner from '../components/ui/UiSpinner.vue';
import { labStatusService } from '../utils/apiService.js';
import { formatDate } from '../utils/dateUtils.js';
import { UNAVAILABLE, switchState } from '../utils/switchState.js';
import { switchLabel } from '../utils/switchLabel.js';
import { isMe } from '../auth.js';
import { toast } from '../composables/toast.js';
import { isUnreachable, reportPoll } from '../composables/poll.js';
import { time, useSortableTable } from '../composables/sortableTable.js';

const switches = ref([]);
const sweep = ref(null);           // the last Sweep's report, when it found something wrong
const isLoading = ref(false);
const loaded = ref(false);         // the first answer has come
const expanded = ref(null);
const rechecking = ref(null);      // the id of the Switch being Re-checked
let refreshTimer = null;

// Each entry of a Switch's history (see CONTEXT.md)
const kindLabels = {
  inspection: 'Inspection',
  release: 'Release',
  cleanup: 'Cleanup',
  quarantine: 'Quarantine',
  quarantine_lifted: 'Quarantine lifted',
  out_of_service: 'Out of service',
  back_in_service: 'Back in service',
};

const load = async ({ background = false } = {}) => {
  isLoading.value = !background;
  const result = await labStatusService.get();
  isLoading.value = false;
  if (result.success) {
    switches.value = result.data.switches;
    sweep.value = result.data.sweep || null;
    loaded.value = true;
  }
  if (background || isUnreachable(result)) {
    reportPoll(result);
  } else if (!result.success) {
    toast.error(`Couldn't load the Lab status. ${result.message}`);
  }
  // A Cleanup takes minutes: follow it until it is done
  clearTimeout(refreshTimer);
  if (switches.value.some(s => s.cleaning_up)) {
    refreshTimer = setTimeout(() => load({ background: true }), 15000);
  }
};

// Re-check: an Inspection that lifts the Quarantine if the Switch is clean. Anyone may ask.
const recheck = async (s) => {
  rechecking.value = s.id;
  const result = await labStatusService.recheck(s.id);
  rechecking.value = null;
  if (!result.success) {
    toast.error(`Couldn't Re-check ${switchLabel(s)}. ${result.message}`);
  } else if (result.data.clean) {
    toast.success(result.data.detail);
  } else {
    toast.error(result.data.detail);  // still not clean: stays until read
  }
  await load();
};

// --- The Sweep report: one line until opened, and it stays as the user left it ---
const SWEEP_OPEN_KEY = 'labStatus.sweepOpen';
const sweepOpen = ref(localStorage.getItem(SWEEP_OPEN_KEY) === '1');
const toggleSweep = () => {
  sweepOpen.value = !sweepOpen.value;
  localStorage.setItem(SWEEP_OPEN_KEY, sweepOpen.value ? '1' : '0');
};

// --- What the last Inspection found: clean, not clean, or never inspected ---
const inspectionState = (s) => {
  if (!s.inspection) return 'never';
  return s.inspection.ok ? 'clean' : 'dirty';
};

const INSPECTION = {
  clean: { label: 'Clean', tone: 'success' },
  dirty: { label: 'Not clean', tone: 'warning' },
  never: { label: 'Never inspected', tone: 'neutral' },
};

// --- Filters: the counts, pressed or not, and a search. A pressed filter takes the colours
// of the badge it stands for (UiBadge tones). ---
const FILTERS = [
  { key: 'clean', label: 'clean', keeps: s => inspectionState(s) === 'clean',
    dot: 'bg-success-500', pressedClass: 'bg-success-100 text-success-800 ring-success-600' },
  { key: 'dirty', label: 'not clean', keeps: s => inspectionState(s) === 'dirty',
    dot: 'bg-warning-400', pressedClass: 'bg-warning-100 text-warning-800 ring-warning-500' },
  { key: 'never', label: 'never inspected', keeps: s => inspectionState(s) === 'never',
    dot: 'bg-gray-400', pressedClass: 'bg-gray-100 text-gray-800 ring-gray-500' },
  { key: 'quarantine', label: `in ${UNAVAILABLE.quarantine.label}`, keeps: s => !!s.quarantine,
    dot: 'bg-warning-500', pressedClass: 'bg-warning-500 text-white ring-warning-600' },
  { key: 'out_of_service', label: UNAVAILABLE.out_of_service.label, keeps: s => !!s.out_of_service,
    dot: 'bg-gray-700', pressedClass: 'bg-gray-700 text-white ring-gray-800' },
];
const pressed = ref([]);
const searchText = ref('');

const filters = computed(() => FILTERS.map(f => ({ ...f, count: switches.value.filter(f.keeps).length })));

const toggleFilter = (key) => {
  pressed.value = pressed.value.includes(key) ? pressed.value.filter(k => k !== key) : [...pressed.value, key];
};

const clearFilters = () => {
  pressed.value = [];
  searchText.value = '';
};

const matches = (s, text) => [switchLabel(s), s.mngt_IP, s.holder].some(field => (field || '').toLowerCase().includes(text));

// --- Sorting: one column at a time; empty values always last ---
const COLUMNS = [
  { key: 'switch', label: 'Switch', sorts: true },
  { key: 'state', label: 'State', sorts: true },
  { key: 'holder', label: 'Holder', sorts: true },
  { key: 'until', label: 'Until', sorts: true },
  { key: 'inspection', label: 'Last Inspection', sorts: true },
  { key: 'findings', label: 'Findings', sorts: true },
  { key: 'details', label: '', sorts: false },
];
// Most urgent first when ascending: unlike switchState.js, where a Reservation wins, a
// Quarantine or Out of service comes before who holds the Switch
const stateRank = (s) => {
  if (s.quarantine) return 0;
  if (s.out_of_service) return 1;
  if (s.cleaning_up) return 2;
  if (s.holder) return isMe(s.holder_id) ? 3 : 4;
  return 5;
};

// Not clean, never inspected, clean; then the latest first
const INSPECTION_RANK = { dirty: 0, never: 1, clean: 2 };
const inspectionRank = (s) => [INSPECTION_RANK[inspectionState(s)], -(time(s.inspection?.at) ?? 0)];

// The value a column sorts on, or null for an empty cell
const SORT_VALUES = {
  switch: s => [s.model, s.id],
  state: stateRank,
  holder: s => s.holder ? s.holder.toLowerCase() : null,
  until: s => time(s.end_date),
  inspection: inspectionRank,
  findings: s => {
    const reasons = s.inspection?.reasons?.length || 0;
    const warnings = s.inspection?.warnings?.length || 0;
    return reasons + warnings ? [-reasons, -warnings] : null;
  },
};

const { sort, sortBy, sortIcon, ariaSort, sorted } = useSortableTable(SORT_VALUES, { key: 'switch' }, s => [s.model, s.id]);

const shown = computed(() => {
  const text = searchText.value.trim().toLowerCase();
  const keeps = FILTERS.filter(f => pressed.value.includes(f.key)).map(f => f.keeps);
  return sorted(switches.value.filter(s => (!keeps.length || keeps.some(k => k(s))) && (!text || matches(s, text))));
});

// What the last Inspection found: its reasons, then its warnings
const findings = (s) => {
  return [
    ...(s.inspection?.reasons || []).map(text => ({ text, reason: true })),
    ...(s.inspection?.warnings || []).map(text => ({ text, reason: false })),
  ];
};

const hasDetails = (s) => s.history.length > 0 || findings(s).length > 1;

// Reserved by me, Reserved by <holder>, or Free
const mainState = (s) => switchState({ holder: s.holder, mine: isMe(s.holder_id) });

const toggle = (id) => {
  expanded.value = expanded.value === id ? null : id;
};

onMounted(() => load());
onUnmounted(() => clearTimeout(refreshTimer));
</script>
