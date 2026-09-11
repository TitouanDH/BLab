<template>
  <main class="min-h-screen bg-slate-50 px-6 py-10 lg:px-8">
    <div class="mx-auto max-w-7xl">
      <div class="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p class="text-sm font-semibold uppercase tracking-wide text-teal-700">Lab operations</p>
          <h1 class="mt-2 text-3xl font-bold text-slate-900">Lab health</h1>
          <p class="mt-2 text-slate-600">Live device state, cleanup blockers, and unresolved findings.</p>
        </div>
        <button
          type="button"
          class="rounded-md bg-teal-700 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-800 disabled:opacity-50"
          :disabled="loading"
          @click="loadHealth"
        >
          {{ loading ? 'Refreshing...' : 'Refresh' }}
        </button>
      </div>

      <p v-if="error" class="mb-6 rounded-md border border-red-200 bg-red-50 p-4 text-red-800">{{ error }}</p>

      <section v-if="summary" class="mb-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div class="rounded-lg border border-slate-200 bg-white p-4">
          <p class="text-sm text-slate-500">Overall lab state</p>
          <p :class="overallClass" class="mt-1 text-2xl font-bold">{{ summary.overall }}</p>
        </div>
        <div v-for="state in summaryStates" :key="state.name" class="rounded-lg border border-slate-200 bg-white p-4">
          <p class="text-sm text-slate-500">{{ state.label }}</p>
          <p class="mt-1 text-2xl font-bold text-slate-900">{{ state.count }}</p>
        </div>
      </section>

      <div class="mb-6 flex flex-wrap items-center gap-3">
        <label for="health-filter" class="text-sm font-medium text-slate-700">Filter</label>
        <select id="health-filter" v-model="filter" class="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm">
          <option value="ALL">All devices</option>
          <option value="UNKNOWN">Unverified</option>
          <option value="HEALTHY">Ready</option>
          <option value="DIRTY">Dirty</option>
          <option value="UNREACHABLE">Unreachable</option>
          <option value="QUARANTINED">Quarantined</option>
        </select>
      </div>

      <div v-if="!loading && health.length === 0" class="rounded-lg border border-slate-200 bg-white p-8 text-center text-slate-600">
        No switches are currently registered.
      </div>

      <div class="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
        <article v-for="entry in filteredHealth" :key="entry.switch.id" class="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
          <div class="flex items-start justify-between gap-4">
            <div>
              <h2 class="font-semibold text-slate-900">{{ entry.switch.model }}</h2>
              <p class="mt-1 text-sm text-slate-500">{{ entry.switch.mngt_IP }}</p>
            </div>
            <span :class="statusClass(entry.switch.health_state)" class="rounded-full px-2.5 py-1 text-xs font-semibold">
              {{ formatState(entry.switch.health_state) }}
            </span>
          </div>

          <dl class="mt-5 grid grid-cols-2 gap-4 text-sm">
            <div>
              <dt class="text-slate-500">Platform</dt>
              <dd class="mt-1 font-medium text-slate-900">{{ entry.switch.platform }} {{ entry.switch.software_version }}</dd>
            </div>
            <div>
              <dt class="text-slate-500">Last check</dt>
              <dd class="mt-1 font-medium text-slate-900">{{ formatDate(entry.switch.last_health_check) }}</dd>
            </div>
            <div>
              <dt class="text-slate-500">Last verified clean</dt>
              <dd class="mt-1 font-medium text-slate-900">{{ formatDate(entry.switch.last_verified_clean) }}</dd>
            </div>
            <div>
              <dt class="text-slate-500">Findings</dt>
              <dd class="mt-1 font-medium text-slate-900">{{ entry.findings.length }}</dd>
            </div>
            <div>
              <dt class="text-slate-500">BLab links</dt>
              <dd class="mt-1 font-medium text-slate-900">{{ entry.active_blab_links }}</dd>
            </div>
          </dl>

          <div class="mt-5 grid grid-cols-2 gap-3 border-t border-slate-100 pt-4 text-xs">
            <div>
              <p class="text-slate-500">User equipment</p>
              <p class="mt-1 font-semibold text-slate-800">{{ formatState(entry.user_equipment_state) }}</p>
            </div>
            <div>
              <p class="text-slate-500">Backbone service</p>
              <p class="mt-1 font-semibold text-slate-800">{{ formatState(entry.backbone_state) }}</p>
            </div>
          </div>

          <ul v-if="entry.findings.length" class="mt-5 space-y-2 border-t border-slate-100 pt-4">
            <li v-for="finding in entry.findings.slice(0, 4)" :key="finding.id" class="text-sm">
              <span class="font-semibold text-amber-700">{{ finding.category }}</span>
              <span class="text-slate-700">: {{ finding.message }}</span>
            </li>
          </ul>
          <p v-else class="mt-5 border-t border-slate-100 pt-4 text-sm text-emerald-700">No unresolved findings.</p>
        </article>
      </div>
    </div>
  </main>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { healthService } from '../utils/apiService.js'

const health = ref([])
const summary = ref(null)
const filter = ref('ALL')
const loading = ref(false)
const error = ref('')
let refreshTimer

const loadHealth = async () => {
  loading.value = true
  error.value = ''
  const result = await healthService.getAll()
  if (result.success) {
    health.value = result.data.health || []
    summary.value = result.data.summary || null
  } else {
    error.value = result.message || 'Unable to load lab health.'
  }
  loading.value = false
}

const formatState = (state) => (state || 'UNKNOWN').replaceAll('_', ' ')
const formatDate = (value) => value ? new Date(value).toLocaleString() : 'Never'
const filteredHealth = computed(() => filter.value === 'ALL'
  ? health.value
  : health.value.filter(entry => entry.switch.health_state === filter.value))
const summaryStates = computed(() => [
  { name: 'ready', label: 'Ready', count: summary.value?.states?.HEALTHY || 0 },
  { name: 'attention', label: 'Needs attention', count: (summary.value?.states?.DIRTY || 0) + (summary.value?.states?.UNKNOWN || 0) },
  { name: 'reserved', label: 'Reserved', count: summary.value?.states?.RESERVED || 0 },
])
const overallClass = computed(() => summary.value?.overall === 'HEALTHY' ? 'text-emerald-700' : summary.value?.overall === 'CRITICAL' ? 'text-red-700' : 'text-amber-700')
const statusClass = (state) => {
  if (state === 'HEALTHY') return 'bg-emerald-100 text-emerald-800'
  if (['DIRTY', 'UNREACHABLE', 'QUARANTINED'].includes(state)) return 'bg-red-100 text-red-800'
  if (['CLEANING', 'CLEANUP_PENDING', 'STALE'].includes(state)) return 'bg-amber-100 text-amber-800'
  return 'bg-slate-100 text-slate-700'
}

onMounted(() => {
  loadHealth()
  refreshTimer = window.setInterval(loadHealth, 30000)
})

onUnmounted(() => window.clearInterval(refreshTimer))
</script>
