<template>
  <!-- Where the Topology starts: a card per Project of mine (with Projects on), or my Topology
       and those shared with me (off). Each opens its canvas at a bookmarkable address:
       /topology/<project id>, or /topology/user/<owner id>. -->
  <div class="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
    <div class="mb-6 flex flex-wrap items-baseline justify-between gap-3">
      <div>
        <h1 class="text-2xl font-bold text-gray-900">Topology</h1>
        <p class="mt-1 text-sm text-gray-600">{{ projectsOn ? 'Choose a Project to see its Switches and Links.' : 'Choose a Topology to see its Switches and Links.' }}</p>
      </div>
      <router-link v-if="projectsOn" to="/projects" class="text-sm font-semibold text-primary-700 hover:text-primary-800">
        Members and Invite links <span aria-hidden="true">&rarr;</span>
      </router-link>
    </div>

    <div v-if="loading" class="flex justify-center py-16 text-primary-700"><UiSpinner size="lg" /></div>

    <div v-else-if="failed" class="rounded-lg bg-white p-6 text-center shadow-sm ring-1 ring-gray-200">
      <p class="text-gray-700">{{ failed }}</p>
      <UiButton class="mt-3" @click="load">Try again</UiButton>
    </div>

    <div v-else-if="!entries.length" class="rounded-lg bg-white p-8 text-center shadow-sm ring-1 ring-gray-200">
      <p class="font-medium text-gray-900">You are in no Project yet.</p>
      <p class="mt-1 text-gray-600">
        Your first Reservation on the <router-link to="/reservation" class="font-medium text-primary-700 underline">Reservation</router-link>
        page makes one, or join a Project through its Invite link.
      </p>
    </div>

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
            <p v-if="entry.reference" class="text-sm text-gray-500">{{ entry.reference }}</p>
            <p class="mt-2 text-sm text-gray-600">{{ entry.summary() }}</p>
            <div v-if="entry.members?.length" class="mt-3 flex items-center gap-1" :aria-label="`Members: ${entry.members.map(m => m.username).join(', ')}`">
              <span
                v-for="m in entry.members.slice(0, 6)"
                :key="m.id"
                :title="m.username"
                class="flex h-7 w-7 items-center justify-center rounded-full bg-primary-100 text-xs font-bold text-primary-800 ring-2 ring-white"
                aria-hidden="true"
              >{{ m.username[0].toUpperCase() }}</span>
              <span v-if="entry.members.length > 6" class="text-xs text-gray-500" aria-hidden="true">+{{ entry.members.length - 6 }}</span>
            </div>
          </div>
        </router-link>
      </li>
    </ul>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue';
import TopologyPreview from '../components/topology/TopologyPreview.vue';
import UiButton from '../components/ui/UiButton.vue';
import UiSpinner from '../components/ui/UiSpinner.vue';
import { projectService, topologyService } from '../utils/apiService.js';
import { loadProjects } from '../utils/projects.js';
import { getCurrentUserId, session } from '../auth.js';
import { plural } from '../utils/plural.js';

const loading = ref(true);
const failed = ref('');
const projectsOn = ref(!!session.projects);
const projects = ref([]);
const shared = ref([]);
// The Topology of each entry, by key, for its preview and counts: null when it couldn't load
const previews = reactive({});

const ownCount = (key) => (previews[key]?.switches || []).filter(sw => sw.in_topology).length;

const entries = computed(() => {
  if (projectsOn.value) {
    return projects.value.map(p => ({
      key: `p${p.id}`,
      to: `/topology/${p.id}`,
      name: p.name,
      reference: p.reference,
      members: p.members,
      summary: () => `${plural(p.switch_count, 'Switch', 'Switches')}, ${plural(p.members.length, 'member', 'members')}`,
      load: () => projectService.topology(p.id),
    }));
  }
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
  failed.value = '';
  const result = await loadProjects();
  projectsOn.value = result.enabled ?? !!session.projects;
  if (projectsOn.value) {
    if (!result.result.success) {
      failed.value = `Couldn't load your Projects. ${result.result.message || ''}`.trim();
      loading.value = false;
      return;
    }
    projects.value = result.active;
  } else {
    const sharedResult = await topologyService.getShared();
    shared.value = sharedResult.success ? sharedResult.data?.shared_with_me || [] : [];
  }
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
