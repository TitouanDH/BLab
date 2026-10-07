<template>
  <div>
    <Navbar />
    <div class="container mx-auto px-4 py-8">
      <div class="flex items-center justify-between mb-6">
        <div>
          <h1 class="text-2xl font-bold text-gray-900">Lab status</h1>
          <p class="text-sm text-gray-600">
            Every switch, who holds it, and what its last Inspection found.
            An Inspection only reads the switch.
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
      </div>

      <p v-if="error" class="mb-4 text-red-700">{{ error }}</p>
      <p v-if="isLoading && !switches.length" class="text-gray-500">Loading...</p>

      <div class="overflow-x-auto bg-white rounded-lg shadow">
        <table class="min-w-full text-sm text-left">
          <thead class="bg-gray-50 text-gray-700">
            <tr>
              <th class="px-4 py-3">Switch</th>
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
                <td colspan="6" class="px-4 py-3">
                  <ul class="space-y-1">
                    <li v-for="(e, i) in s.history" :key="i">
                      <span class="text-gray-500">{{ formatDate(e.at) }}</span>
                      <span class="ml-2 font-medium capitalize">{{ e.kind }}</span>:
                      <span :class="e.ok ? 'text-green-700' : 'text-red-700'">{{ e.ok ? 'ok' : e.reasons.join('; ') }}</span>
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
import { computed, onMounted, ref } from 'vue';
import Navbar from '../components/Navbar.vue';
import { labStatusService } from '../utils/apiService.js';

const switches = ref([]);
const isLoading = ref(false);
const error = ref('');
const expanded = ref(null);

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
};

const counts = computed(() => ({
  clean: switches.value.filter(s => s.inspection && s.inspection.ok).length,
  dirty: switches.value.filter(s => s.inspection && !s.inspection.ok).length,
  never: switches.value.filter(s => !s.inspection).length,
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
</script>
