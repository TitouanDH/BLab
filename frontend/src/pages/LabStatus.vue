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

    <div class="mb-4 flex flex-wrap gap-2 text-sm">
      <UiBadge tone="success">{{ counts.clean }} clean</UiBadge>
      <UiBadge tone="warning">{{ counts.dirty }} not clean</UiBadge>
      <UiBadge>{{ counts.never }} never inspected</UiBadge>
      <UiBadge tone="strong-warning">{{ counts.quarantine }} in Quarantine</UiBadge>
      <UiBadge tone="dark">{{ counts.outOfService }} Out of service</UiBadge>
    </div>

    <div v-if="!loaded" class="flex justify-center py-16 text-primary-700"><UiSpinner size="lg" /></div>

    <div v-else class="overflow-x-auto rounded-lg bg-white shadow-sm ring-1 ring-gray-200">
      <table class="min-w-full text-left text-sm">
        <thead class="bg-gray-50 text-gray-700">
          <tr>
            <th class="px-4 py-3">Switch</th>
            <th class="px-4 py-3">State</th>
            <th class="px-4 py-3">Holder</th>
            <th class="px-4 py-3">Until</th>
            <th class="px-4 py-3">Last Inspection</th>
            <th class="px-4 py-3">Findings</th>
            <th class="px-4 py-3"></th>
          </tr>
        </thead>
        <tbody>
          <template v-for="s in switches" :key="s.id">
            <tr class="border-t align-top">
              <td class="px-4 py-3">
                <div class="font-medium text-gray-900">{{ s.model }}</div>
                <div class="text-gray-500">{{ s.mngt_IP }}</div>
              </td>
              <td class="max-w-xs px-4 py-3" :data-switch="s.mngt_IP">
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
                <UiBadge :tone="inspectionTone(s.inspection)">{{ resultLabel(s.inspection) }}</UiBadge>
                <div v-if="s.inspection" class="mt-1 text-xs text-gray-500">{{ formatDate(s.inspection.at) }}</div>
              </td>
              <td class="px-4 py-3">
                <ul v-if="s.inspection">
                  <li v-for="reason in s.inspection.reasons" :key="reason" class="text-warning-800">{{ reason }}</li>
                  <li v-for="warning in s.inspection.warnings" :key="warning" class="break-all text-gray-600">
                    {{ warning }}
                  </li>
                </ul>
              </td>
              <td class="px-4 py-3">
                <button v-if="s.history.length" type="button" class="text-primary-700 hover:underline" @click="toggle(s.id)">
                  {{ expanded === s.id ? 'Hide history' : 'History' }}
                </button>
              </td>
            </tr>
            <tr v-if="expanded === s.id" class="bg-gray-50">
              <td colspan="7" class="px-4 py-3">
                <ul class="space-y-1">
                  <li v-for="(e, i) in s.history" :key="i">
                    <span class="text-gray-500">{{ formatDate(e.at) }}</span>
                    <span class="ml-2 font-medium">{{ kindLabels[e.kind] || e.kind }}</span>
                    <span v-if="e.user" class="text-gray-500"> ({{ e.user }})</span>:
                    <span :class="e.ok ? 'text-success-700' : 'text-warning-800'">{{ e.reasons.length ? e.reasons.join('; ') : (e.ok ? 'ok' : 'failed') }}</span>
                    <span v-if="e.warnings.length" class="text-gray-600"> ({{ e.warnings.length }} warning(s))</span>
                  </li>
                </ul>
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
import UiBadge from '../components/ui/UiBadge.vue';
import UiButton from '../components/ui/UiButton.vue';
import UiSpinner from '../components/ui/UiSpinner.vue';
import { labStatusService } from '../utils/apiService.js';
import { formatDate } from '../utils/dateUtils.js';
import { UNAVAILABLE, switchState } from '../utils/switchState.js';
import { isMe } from '../auth.js';
import { toast } from '../composables/toast.js';
import { isUnreachable, reportPoll } from '../composables/poll.js';

const switches = ref([]);
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
    toast.error(`Couldn't Re-check ${s.mngt_IP}. ${result.message}`);
  } else if (result.data.clean) {
    toast.success(result.data.detail);
  } else {
    toast.error(result.data.detail);  // still not clean: stays until read
  }
  await load();
};

const counts = computed(() => ({
  clean: switches.value.filter(s => s.inspection && s.inspection.ok).length,
  dirty: switches.value.filter(s => s.inspection && !s.inspection.ok).length,
  never: switches.value.filter(s => !s.inspection).length,
  quarantine: switches.value.filter(s => s.quarantine).length,
  outOfService: switches.value.filter(s => s.out_of_service).length,
}));

// Reserved by me, Reserved by <holder>, or Free
const mainState = (s) => switchState({ holder: s.holder, mine: isMe(s.holder_id) });

const toggle = (id) => {
  expanded.value = expanded.value === id ? null : id;
};

const resultLabel = (inspection) => {
  if (!inspection) return 'Never inspected';
  return inspection.ok ? 'Clean' : 'Not clean';
};

const inspectionTone = (inspection) => {
  if (!inspection) return 'neutral';
  return inspection.ok ? 'success' : 'warning';
};

onMounted(() => load());
onUnmounted(() => clearTimeout(refreshTimer));
</script>
