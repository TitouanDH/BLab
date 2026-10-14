<template>
  <!-- Where the Topology starts while BLab has no Projects: my Topology and those shared with
       me, each opening its canvas at a bookmarkable address, /topology/user/<owner id>. With
       Projects on, a Topology is a Project's picture, on the Project's page (Projects.vue). -->
  <div class="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
    <div class="mb-6 flex flex-wrap items-baseline justify-between gap-3">
      <div>
        <h1 class="text-2xl font-bold text-gray-900">Topology</h1>
        <p class="mt-1 text-sm text-gray-600">Choose a Topology to see its Switches and Links.</p>
      </div>
    </div>

    <div v-if="loading" class="flex justify-center py-16 text-primary-700"><UiSpinner size="lg" /></div>

    <ul v-else class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-label="Topologies">
      <li v-for="entry in entries" :key="entry.key">
        <router-link
          :to="entry.to"
          class="flex h-full flex-col rounded-lg bg-white shadow-sm ring-1 ring-gray-200 transition hover:shadow-md hover:ring-primary-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-600"
          :data-topology="entry.key"
        >
          <div class="border-b border-gray-100 bg-gray-50 px-3 py-2">
            <TopologyPreview v-if="previews[entry.key]" :topology="previews[entry.key]" />
            <div v-else-if="previews[entry.key] === null" class="flex h-28 items-center justify-center text-xs text-gray-500">No preview</div>
            <div v-else class="flex h-28 items-center justify-center text-primary-700"><UiSpinner /></div>
          </div>
          <div class="flex flex-1 flex-col p-4">
            <h2 class="font-semibold text-gray-900">{{ entry.name }}</h2>
            <p class="mt-2 text-sm text-gray-600">{{ entry.summary() }}</p>
          </div>
        </router-link>
      </li>
    </ul>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import TopologyPreview from '../components/topology/TopologyPreview.vue';
import UiSpinner from '../components/ui/UiSpinner.vue';
import { topologyService } from '../utils/apiService.js';
import { loadProjects } from '../utils/projects.js';
import { getCurrentUserId } from '../auth.js';
import { plural } from '../utils/plural.js';

const route = useRoute();
const router = useRouter();
const loading = ref(true);
const shared = ref([]);
// The Topology of each entry, by key, for its preview and counts: null when it couldn't load
const previews = reactive({});

const ownCount = (key) => (previews[key]?.switches || []).filter(sw => sw.in_topology).length;

const entries = computed(() => {
  const me = getCurrentUserId();
  return [
    { key: `u${me}`, to: `/topology/user/${me}`, name: 'My Topology', owner: me },
    ...shared.value.map(s => ({ key: `u${s.owner_id}`, to: `/topology/user/${s.owner_id}`, name: `${s.owner_username}'s Topology`, owner: s.owner_id })),
  ].map(e => ({
    ...e,
    summary: () => (previews[e.key] ? plural(ownCount(e.key), 'Switch', 'Switches') : ''),
    load: () => topologyService.get(e.owner),
  }));
});

async function load() {
  loading.value = true;
  const [projects, sharedResult] = await Promise.all([loadProjects(), topologyService.getShared()]);
  if (projects.enabled) {
    // Turned on since this browser last asked: the Topologies are on the Projects' pages
    const project = String(route.query.project || '');
    router.replace(/^\d+$/.test(project) ? `/projects/${project}` : '/projects');
    return;
  }
  shared.value = sharedResult.success ? sharedResult.data?.shared_with_me || [] : [];
  loading.value = false;
  // The previews come one by one, as each Topology answers
  for (const entry of entries.value) {
    entry.load()
      .then((answer) => { previews[entry.key] = answer.success ? answer.data : null; })
      .catch(() => { previews[entry.key] = null; });
  }
}

onMounted(load);
</script>
