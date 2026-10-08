<template>
  <div>
    <Navbar />
    <div class="container mx-auto px-4 py-8">
      <div class="flex items-center justify-between mb-6">
        <div>
          <h1 class="text-2xl font-bold text-gray-900">Lab status</h1>
          <p class="text-sm text-gray-600">
            Every switch, who holds it, what its last Inspection found, and whether it is in Quarantine
            or Out of service. An Inspection only reads the switch.
          </p>
        </div>
        <button @click="load" :disabled="isLoading"
                class="px-4 py-2 text-sm text-white bg-teal-700 rounded-lg hover:bg-teal-800 disabled:opacity-50">
          Refresh
        </button>
      </div>

      <div class="flex gap-4 mb-4 text-sm">
        <span class="px-3 py-1 rounded-full bg-green-100 text-green-800">{{ counts.clean }} clean</span>
        <span class="px-3 py-1 rounded-full bg-red-100 text-red-800">{{ counts.dirty }} not clean</span>
        <span class="px-3 py-1 rounded-full bg-gray-100 text-gray-700">{{ counts.never }} never inspected</span>
        <span class="px-3 py-1 rounded-full bg-red-600 text-white">{{ counts.quarantine }} in Quarantine</span>
        <span class="px-3 py-1 rounded-full bg-gray-700 text-white">{{ counts.outOfService }} Out of service</span>
      </div>

      <p v-if="error" class="mb-4 text-red-700">{{ error }}</p>
      <p v-if="isLoading && !switches.length" class="text-gray-500">Loading...</p>

      <div class="overflow-x-auto bg-white rounded-lg shadow">
        <table class="min-w-full text-sm text-left">
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
                <td class="px-4 py-3 max-w-xs">
                  <div v-if="s.out_of_service">
                    <span class="px-2 py-1 rounded-full text-xs font-semibold bg-gray-700 text-white">Out of service</span>
                    <div class="text-xs text-gray-600 mt-1">{{ s.out_of_service.reason }}</div>
                  </div>
                  <div v-if="s.quarantine" :class="{ 'mt-2': s.out_of_service }">
                    <span class="px-2 py-1 rounded-full text-xs font-semibold bg-red-600 text-white">Quarantine</span>
                    <div class="text-xs text-gray-600 mt-1">
                      {{ s.quarantine.holder ? `Names ${s.quarantine.holder}` : 'Names nobody: an admin clears it' }},
                      since {{ formatDate(s.quarantine.opened_at) }}
                    </div>
                    <ul class="text-xs text-red-700 mt-1">
                      <li v-for="reason in s.quarantine.reasons" :key="reason">{{ reason }}</li>
                    </ul>
                    <button v-if="!s.cleaning_up" @click="recheck(s)" :disabled="rechecking === s.id"
                            class="mt-2 px-3 py-1 text-xs text-white bg-teal-700 rounded hover:bg-teal-800 disabled:opacity-50">
                      {{ rechecking === s.id ? 'Re-checking...' : 'Re-check' }}
                    </button>
                    <div v-if="recheckMessages[s.id]" class="text-xs mt-1 text-gray-700">{{ recheckMessages[s.id] }}</div>
                  </div>
                  <div v-if="s.cleaning_up" :class="{ 'mt-2': s.out_of_service || s.quarantine }">
                    <span class="px-2 py-1 rounded-full text-xs font-semibold bg-teal-100 text-teal-800">Being Cleaned up</span>
                    <div class="text-xs text-gray-600 mt-1">Reloading, then Inspected</div>
                  </div>
                  <span v-if="!s.out_of_service && !s.quarantine && !s.cleaning_up" class="text-gray-400">-</span>
                </td>
                <td class="px-4 py-3">{{ s.holder || '-' }}</td>
                <td class="px-4 py-3">{{ s.end_date ? formatDate(s.end_date) : '-' }}</td>
                <td class="px-4 py-3">
                  <span :class="['px-2 py-1 rounded-full text-xs font-semibold', badgeClass(s.inspection)]">
                    {{ resultLabel(s.inspection) }}
                  </span>
                  <div v-if="s.inspection" class="text-xs text-gray-500 mt-1">{{ formatDate(s.inspection.at) }}</div>
                </td>
                <td class="px-4 py-3">
                  <ul v-if="s.inspection">
                    <li v-for="reason in s.inspection.reasons" :key="reason" class="text-red-700">{{ reason }}</li>
                    <li v-for="warning in s.inspection.warnings" :key="warning" class="text-amber-700 break-all">
                      {{ warning }}
                    </li>
                  </ul>
                </td>
                <td class="px-4 py-3">
                  <button v-if="s.history.length" @click="toggle(s.id)" class="text-teal-700 hover:underline">
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
                      <span :class="e.ok ? 'text-green-700' : 'text-red-700'">{{ e.reasons.length ? e.reasons.join('; ') : (e.ok ? 'ok' : 'failed') }}</span>
                      <span v-if="e.warnings.length" class="text-amber-700"> ({{ e.warnings.length }} warning(s))</span>
                    </li>
                  </ul>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue';
import Navbar from '../components/Navbar.vue';
import { labStatusService } from '../utils/apiService.js';

const switches = ref([]);
const isLoading = ref(false);
const error = ref('');
const expanded = ref(null);
const rechecking = ref(null);      // the id of the Switch being Re-checked
const recheckMessages = ref({});   // by Switch id: what the last Re-check found
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

const load = async () => {
  isLoading.value = true;
  const result = await labStatusService.get();
  isLoading.value = false;
  if (result.success) {
    switches.value = result.data.switches;
    error.value = '';
  } else {
    error.value = result.message || 'Failed to load the lab status.';
  }
  // A Cleanup takes minutes: follow it until it is done
  clearTimeout(refreshTimer);
  if (switches.value.some(s => s.cleaning_up)) {
    refreshTimer = setTimeout(load, 15000);
  }
};

// Re-check: an Inspection that lifts the Quarantine if the Switch is clean. Anyone may ask.
const recheck = async (s) => {
  rechecking.value = s.id;
  const result = await labStatusService.recheck(s.id);
  rechecking.value = null;
  recheckMessages.value = {
    ...recheckMessages.value,
    [s.id]: result.success ? result.data.detail : `Re-check failed: ${result.message}`,
  };
  await load();
};

const counts = computed(() => ({
  clean: switches.value.filter(s => s.inspection && s.inspection.ok).length,
  dirty: switches.value.filter(s => s.inspection && !s.inspection.ok).length,
  never: switches.value.filter(s => !s.inspection).length,
  quarantine: switches.value.filter(s => s.quarantine).length,
  outOfService: switches.value.filter(s => s.out_of_service).length,
}));

const toggle = (id) => {
  expanded.value = expanded.value === id ? null : id;
};

const resultLabel = (inspection) => {
  if (!inspection) return 'Never inspected';
  return inspection.ok ? 'Clean' : 'Not clean';
};

const badgeClass = (inspection) => {
  if (!inspection) return 'bg-gray-100 text-gray-700';
  return inspection.ok ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800';
};

const formatDate = (value) => new Date(value).toLocaleString('en-GB', {
  year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
});

onMounted(load);
onUnmounted(() => clearTimeout(refreshTimer));
</script>
