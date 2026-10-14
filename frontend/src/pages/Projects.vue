<template>
  <!-- My Projects (CONTEXT.md): a card per Project, with a preview of its Topology, opening its
       page (/projects/<id>). Create a Project here; its owner archives it here once it holds no
       Switch. -->
  <div class="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
    <div class="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="text-2xl font-bold text-gray-900">My Projects</h1>
        <p class="text-sm text-gray-600">
          A Project is a piece of work: the Switches reserved into it and its members, who may all work on them.
        </p>
      </div>
      <UiButton v-if="enabled" variant="primary" @click="creating = true">New Project</UiButton>
    </div>

    <div v-if="!loaded" class="flex justify-center py-16 text-primary-700"><UiSpinner size="lg" /></div>
    <template v-else-if="enabled">
      <div v-if="!active.length" class="rounded-lg bg-white p-8 text-center shadow-sm ring-1 ring-gray-200" data-testid="no-projects">
        <p class="font-medium text-gray-900">You are in no Project yet.</p>
        <p class="mt-1 text-gray-600">
          Create one, or join one through its Invite link. Your first Reservation also makes one for you.
        </p>
      </div>
      <ul v-else class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-label="Projects">
        <li v-for="p in active" :key="p.id" :data-project="p.id" class="flex flex-col rounded-lg bg-white shadow-sm ring-1 ring-gray-200 transition hover:shadow-md hover:ring-primary-600">
          <router-link
            :to="`/projects/${p.id}`"
            class="flex flex-1 flex-col rounded-t-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-600"
          >
            <div class="rounded-t-lg border-b border-gray-100 bg-gray-50 px-3 py-2">
              <TopologyPreview v-if="previews[p.id]" :topology="previews[p.id]" />
              <div v-else-if="previews[p.id] === null" class="flex h-28 items-center justify-center text-xs text-gray-500">No preview</div>
              <div v-else class="flex h-28 items-center justify-center text-primary-700"><UiSpinner /></div>
            </div>
            <div class="flex flex-1 flex-col px-4 pt-4">
              <h2 class="font-semibold text-gray-900">{{ p.name }}</h2>
              <p v-if="p.reference" class="text-sm text-gray-500">{{ p.reference }}</p>
              <p class="mt-2 text-sm text-gray-600" data-testid="project-counts">
                {{ plural(p.switch_count, 'Switch', 'Switches') }}, {{ plural(p.members.length, 'member', 'members') }}
              </p>
            </div>
          </router-link>
          <div class="flex items-center justify-between gap-2 px-4 pb-4 pt-3">
            <div class="flex items-center gap-1" :aria-label="`Members: ${p.members.map(m => m.username).join(', ')}`">
              <span
                v-for="m in p.members.slice(0, 6)"
                :key="m.id"
                :title="m.username"
                class="flex h-7 w-7 items-center justify-center rounded-full bg-primary-100 text-xs font-bold text-primary-800 ring-2 ring-white"
                aria-hidden="true"
              >{{ m.username[0].toUpperCase() }}</span>
              <span v-if="p.members.length > 6" class="text-xs text-gray-500" aria-hidden="true">+{{ p.members.length - 6 }}</span>
            </div>
            <UiButton
              v-if="p.is_owner" size="sm" :disabled="p.switch_count > 0"
              :title="p.switch_count > 0 ? 'Release its Switches first' : 'Archive this Project'"
              @click="askArchive(p)"
            >Archive</UiButton>
          </div>
        </li>
      </ul>

      <section v-if="archived.length" class="mt-8" aria-labelledby="archived-heading">
        <h2 id="archived-heading" class="mb-2 text-lg font-semibold text-gray-900">Archived</h2>
        <ul class="divide-y divide-gray-200 rounded-lg bg-white text-sm shadow-sm ring-1 ring-gray-200">
          <li v-for="p in archived" :key="p.id" :data-project="p.id">
            <router-link :to="`/projects/${p.id}/snapshots`" class="flex justify-between p-3 text-gray-600 hover:bg-gray-50">
              <span>{{ p.name }}<span v-if="p.reference"> · {{ p.reference }}</span></span>
              <span>Archived {{ formatDate(p.archived_at) }}</span>
            </router-link>
          </li>
        </ul>
      </section>
    </template>

    <!-- A new Project, without reserving yet: it opens on its empty Topology -->
    <UiModal v-if="creating" title="New Project" @close="creating = false">
      <form id="new-project" class="space-y-4" @submit.prevent="create">
        <div>
          <label for="project-name" class="mb-1 block font-medium text-gray-700">Name</label>
          <input id="project-name" v-model.trim="newName" maxlength="100" required class="input" />
        </div>
        <div>
          <label for="project-reference" class="mb-1 block font-medium text-gray-700">Reference (optional)</label>
          <input id="project-reference" v-model.trim="newReference" maxlength="100" placeholder="A support case, say" class="input" />
        </div>
      </form>
      <template #actions>
        <UiButton @click="creating = false">Cancel</UiButton>
        <UiButton type="submit" form="new-project" variant="primary" :pending="pending" :disabled="!newName">Create</UiButton>
      </template>
    </UiModal>

    <UiConfirm
      v-if="archiving" :title="`Archive ${archiving.name}?`" confirm-label="Archive"
      message="Nobody can reserve into it or join it any more. It stays listed under Archived, with its Snapshots."
      :danger="false" :pending="pending" @close="archiving = null" @confirm="archive"
    />
  </div>
</template>

<script setup>
import { computed, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import TopologyPreview from '../components/topology/TopologyPreview.vue';
import UiButton from '../components/ui/UiButton.vue';
import UiConfirm from '../components/ui/UiConfirm.vue';
import UiModal from '../components/ui/UiModal.vue';
import UiSpinner from '../components/ui/UiSpinner.vue';
import { projectService } from '../utils/apiService.js';
import { formatDate } from '../utils/dateUtils.js';
import { plural } from '../utils/plural.js';
import { loadProjects } from '../utils/projects.js';
import { toast } from '../composables/toast.js';
import { isUnreachable, usePoll } from '../composables/poll.js';

const router = useRouter();
const loaded = ref(false);
const enabled = ref(null);
const projects = ref([]);
const active = computed(() => projects.value.filter(p => !p.archived_at));
const archived = computed(() => projects.value.filter(p => p.archived_at));
// The Topology of each active Project, by id, for its preview: null when it couldn't load. Read
// again only when the Project's Switch count changes.
const previews = reactive({});
const previewCounts = {};

const loadPreviews = () => {
  for (const p of active.value.filter(x => previewCounts[x.id] !== x.switch_count)) {
    previewCounts[p.id] = p.switch_count;
    projectService.topology(p.id)
      .then((answer) => { if (answer.success || previews[p.id] === undefined) previews[p.id] = answer.success ? answer.data : null; })
      .catch(() => { if (previews[p.id] === undefined) previews[p.id] = null; });
  }
};

const load = async () => {
  const answer = await loadProjects();
  if (answer.enabled === false) {
    router.replace('/topology');  // a Topology is a user's while BLab has no Projects
    return [answer.result];
  }
  if (answer.enabled) {
    enabled.value = true;
    projects.value = answer.projects;
    loaded.value = true;
    loadPreviews();
  } else if (!isUnreachable(answer.result)) {
    toast.error(`Couldn't load your Projects. ${answer.result.message}`, 'projects-load');
  }
  return [answer.result];
};
const { refresh } = usePoll(load, 15 * 1000);

// --- New Project: it opens on its page ---
const creating = ref(false);
const newName = ref('');
const newReference = ref('');
const pending = ref(false);

const create = async () => {
  if (!newName.value) return;
  pending.value = true;
  const result = await projectService.create(newName.value, newReference.value);
  pending.value = false;
  if (!result.success) {
    toast.error(`Couldn't create the Project. ${result.message}`);
    return;
  }
  toast.success(`${result.data.name} created. Reserve a Switch into it, or invite people from its Members.`);
  router.push(`/projects/${result.data.id}`);
};

// --- Archive, once the Project holds no Switch ---
const archiving = ref(null);
const archive = async () => {
  const p = archiving.value;
  pending.value = true;
  const result = await projectService.archive(p.id);
  pending.value = false;
  archiving.value = null;
  if (result.success) toast.success(`${p.name} archived.`);
  else toast.error(`Couldn't archive the Project. ${result.message}`);
  refresh();
};
const askArchive = (p) => { archiving.value = p; };
</script>
