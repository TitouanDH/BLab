<template>
  <!-- A Project's page (CONTEXT.md), with Projects on: its name, then one section at a time,
       each at its own address: Topology (/projects/<id>, the canvas filling the rest of the
       screen), Switches, Snapshots and Members. Members only: BLab answers 404 to others. -->
  <div class="flex min-h-0 flex-1 flex-col">
    <div class="border-b border-gray-200 bg-white px-4 pt-3 lg:px-8">
      <router-link to="/projects" class="text-sm text-primary-700 hover:underline">&larr; My Projects</router-link>
      <div class="flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
        <div class="min-w-0 pb-2">
          <h1 class="truncate text-xl font-bold text-gray-900">
            {{ project?.name || 'Project' }}
            <UiBadge v-if="project?.archived_at" class="ml-1 align-middle">Archived</UiBadge>
          </h1>
          <p v-if="project" class="truncate text-sm text-gray-600" data-testid="project-summary">{{ summary }}</p>
        </div>
        <nav v-if="project" class="-mb-px flex gap-1 overflow-x-auto" aria-label="Project sections">
          <router-link
            v-for="section in SECTIONS"
            :key="section.name"
            :to="`/projects/${projectId}${section.path}`"
            class="whitespace-nowrap border-b-2 px-3 py-2 text-sm font-semibold transition-colors"
            :class="current === section.path ? 'border-primary-600 text-primary-800' : 'border-transparent text-gray-600 hover:border-gray-300 hover:text-gray-900'"
            :aria-current="current === section.path ? 'page' : undefined"
          >{{ section.name }}</router-link>
        </nav>
      </div>
    </div>

    <div v-if="missing" class="container mx-auto max-w-5xl px-4 py-8">
      <p class="rounded-lg bg-white p-8 text-center text-gray-600 shadow-sm ring-1 ring-gray-200" data-testid="no-such-project">
        No such Project among yours. <router-link to="/projects" class="font-medium text-primary-700 underline">See my Projects</router-link>
      </p>
    </div>
    <div v-else-if="!project" class="flex justify-center py-16 text-primary-700"><UiSpinner size="lg" /></div>
    <!-- Each section gets the Project as projects/<id>/ gives it, and says when it changed it -->
    <router-view v-else v-slot="{ Component }">
      <component :is="Component" :key="projectId" :project="project" @changed="load" />
    </router-view>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import UiBadge from '../components/ui/UiBadge.vue';
import UiSpinner from '../components/ui/UiSpinner.vue';
import { projectService } from '../utils/apiService.js';
import { loadProjects } from '../utils/projects.js';
import { plural } from '../utils/plural.js';
import { toast } from '../composables/toast.js';
import { isUnreachable, usePoll } from '../composables/poll.js';

// The sections, by the end of their address
const SECTIONS = [
  { name: 'Topology', path: '' },
  { name: 'Switches', path: '/switches' },
  { name: 'Snapshots', path: '/snapshots' },
  { name: 'Members', path: '/members' },
];

const route = useRoute();
const router = useRouter();
const projectId = computed(() => String(route.params.id));
const current = computed(() => route.path.slice(`/projects/${projectId.value}`.length).replace(/\/$/, ''));

const project = ref(null);
const missing = ref(false);

const summary = computed(() => {
  const p = project.value;
  return [
    p.reference,
    `Owner: ${p.owner?.username || 'nobody'}`,
    plural(p.switch_count, 'Switch', 'Switches'),
    plural(p.members.length, 'member', 'members'),
  ].filter(Boolean).join(' · ');
});

// Returns the API result, for the poll to tell whether BLab answered. A section's change
// (a Release, say) calls it directly: the poll would skip it while one of its own runs.
const load = async () => {
  const id = projectId.value;
  const result = await projectService.get(id);
  if (id !== projectId.value) return [];  // a late answer for a Project we have since left
  if (result.success) {
    project.value = result.data;
    missing.value = false;
  } else if (result.status === 404) {
    // Not a member (left, removed, or never was), or BLab has no Projects (any more)
    const projects = await loadProjects();
    if (projects.enabled === false) {
      router.replace('/topology');  // its picker says what there is
      return [];
    }
    project.value = null;
    missing.value = true;
  } else if (!isUnreachable(result)) {
    toast.error(`Couldn't load the Project. ${result.message}`, 'project-load');
  }
  return [result];
};
usePoll(load, 10 * 1000);

// Another Project at the same page (a link from one Project to another); none once leaving it
watch(() => route.params.id, (id) => {
  if (!id) return;
  project.value = null;
  missing.value = false;
  load();
});
</script>
