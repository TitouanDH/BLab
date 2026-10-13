<template>
  <!-- My Projects (CONTEXT.md): their members and Invite link. The owner renews the link, removes
       members and archives a Project that holds no Switch; any other member may leave. -->
  <div class="container mx-auto max-w-5xl px-4 py-8">
    <div class="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="text-2xl font-bold text-gray-900">My Projects</h1>
        <p class="text-sm text-gray-600">
          Every member of a Project may work on all its Switches. Invite people with its Invite link;
          you reserve into a Project on the <router-link to="/reservation" class="text-primary-700 hover:underline">Reservation</router-link> page.
        </p>
      </div>
      <UiButton variant="primary" @click="creating = true">New Project</UiButton>
    </div>

    <div v-if="!loaded" class="flex justify-center py-16 text-primary-700"><UiSpinner size="lg" /></div>
    <p v-else-if="enabled === false" class="rounded-lg bg-white p-8 text-center text-gray-600 shadow-sm ring-1 ring-gray-200">
      Projects aren't turned on in this BLab yet.
    </p>
    <template v-else>
      <p v-if="!active.length" class="rounded-lg bg-white p-8 text-center text-gray-600 shadow-sm ring-1 ring-gray-200" data-testid="no-projects">
        You are in no Project yet. Your first Reservation makes one for you, or create one now.
      </p>
      <ul class="space-y-4">
        <li v-for="p in active" :key="p.id" :data-project="p.id" class="rounded-lg bg-white p-4 shadow-sm ring-1 ring-gray-200">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 class="text-lg font-semibold text-gray-900">{{ p.name }}</h2>
              <p class="text-sm text-gray-600">
                <span v-if="p.reference">{{ p.reference }} · </span>
                Owner: {{ p.owner?.username || 'nobody' }} ·
                <span data-testid="switch-count">{{ p.switch_count }} Switch{{ p.switch_count === 1 ? '' : 'es' }}</span>
              </p>
            </div>
            <div class="flex flex-wrap gap-2">
              <UiButton size="sm" :to="{ path: '/topology', query: { project: p.id } }">Open Topology</UiButton>
              <UiButton
                v-if="p.is_owner" size="sm" :disabled="p.switch_count > 0"
                :title="p.switch_count > 0 ? 'Release its Switches first' : 'Archive this Project'"
                @click="ask('archive', p)"
              >Archive</UiButton>
              <UiButton v-else size="sm" variant="danger" @click="ask('leave', p)">Leave</UiButton>
            </div>
          </div>

          <!-- The Invite link: the owner's to share and renew -->
          <div v-if="p.is_owner && p.invite_token" class="mt-3 flex flex-wrap items-center gap-2" data-testid="invite-link">
            <label :for="`invite-${p.id}`" class="text-sm font-medium text-gray-700">Invite link</label>
            <input :id="`invite-${p.id}`" :value="inviteUrl(p.invite_token)" readonly class="input w-auto flex-1 font-mono text-xs" @focus="$event.target.select()" />
            <UiButton size="sm" @click="copy(p)">Copy</UiButton>
            <UiButton size="sm" @click="ask('renew', p)">Renew link</UiButton>
          </div>

          <h3 class="mt-4 text-sm font-semibold text-gray-700">Members</h3>
          <ul class="mt-1 divide-y divide-gray-100 text-sm">
            <li v-for="m in p.members" :key="m.id" :data-member="m.username" class="flex items-center justify-between py-1.5">
              <span>
                {{ m.username }}
                <UiBadge v-if="m.id === p.owner?.id" class="ml-1">Owner</UiBadge>
                <UiBadge v-else-if="isMe(m.id)" class="ml-1">You</UiBadge>
              </span>
              <UiButton v-if="p.is_owner && m.id !== p.owner?.id" size="sm" @click="ask('remove', p, m)">Remove</UiButton>
            </li>
          </ul>
        </li>
      </ul>

      <section v-if="archived.length" class="mt-8" aria-labelledby="archived-heading">
        <h2 id="archived-heading" class="mb-2 text-lg font-semibold text-gray-900">Archived</h2>
        <ul class="divide-y divide-gray-200 rounded-lg bg-white text-sm shadow-sm ring-1 ring-gray-200">
          <li v-for="p in archived" :key="p.id" :data-project="p.id" class="flex justify-between p-3 text-gray-600">
            <span>{{ p.name }}<span v-if="p.reference"> · {{ p.reference }}</span></span>
            <span>Archived {{ formatDate(p.archived_at) }}</span>
          </li>
        </ul>
      </section>
    </template>

    <!-- A new Project, without reserving yet -->
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
      v-if="confirm" :title="confirm.title" :message="confirm.message" :confirm-label="confirm.label"
      :danger="confirm.danger" :pending="pending" @close="confirm = null" @confirm="runConfirm"
    />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue';
import UiBadge from '../components/ui/UiBadge.vue';
import UiButton from '../components/ui/UiButton.vue';
import UiConfirm from '../components/ui/UiConfirm.vue';
import UiModal from '../components/ui/UiModal.vue';
import UiSpinner from '../components/ui/UiSpinner.vue';
import { projectService } from '../utils/apiService.js';
import { formatDate } from '../utils/dateUtils.js';
import { inviteUrl, loadProjects } from '../utils/projects.js';
import { isMe } from '../auth.js';
import { toast } from '../composables/toast.js';
import { isUnreachable, usePoll } from '../composables/poll.js';

const loaded = ref(false);
const enabled = ref(null);
const projects = ref([]);
const active = computed(() => projects.value.filter(p => !p.archived_at));
const archived = computed(() => projects.value.filter(p => p.archived_at));

const load = async () => {
  const answer = await loadProjects();
  if (answer.enabled !== null) {
    enabled.value = answer.enabled;
    projects.value = answer.projects;
    loaded.value = true;
  } else if (!isUnreachable(answer.result)) {
    toast.error(`Couldn't load your Projects. ${answer.result.message}`, 'projects-load');
  }
  return [answer.result];
};
const { refresh } = usePoll(load, 10 * 1000);

// --- New Project ---
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
  toast.success(`${result.data.name} created. Share its Invite link to add members.`);
  creating.value = false;
  newName.value = newReference.value = '';
  refresh();
};

const copy = async (p) => {
  try {
    await navigator.clipboard.writeText(inviteUrl(p.invite_token));
    toast.success('Invite link copied.');
  } catch {
    toast.error('Couldn\'t copy: select the link and copy it yourself.');
  }
};

// --- Renew the link, remove, leave, archive: each asks first ---
const confirm = ref(null);
const ACTIONS = {
  renew: (p) => ({
    title: 'Renew the Invite link?', label: 'Renew', danger: false,
    message: `The current link of ${p.name} stops working: only people you give the new one can join. Members stay.`,
    run: () => projectService.renewInvite(p.id), done: 'Invite link renewed.', failed: 'renew the Invite link',
  }),
  remove: (p, m) => ({
    title: `Remove ${m.username}?`, label: 'Remove', danger: true,
    message: `${m.username} leaves ${p.name}, and BLab removes their Switch accounts on its Switches.`,
    run: () => projectService.removeMember(p.id, m.id), failed: `remove ${m.username}`,
  }),
  leave: (p) => ({
    title: `Leave ${p.name}?`, label: 'Leave', danger: true,
    message: 'BLab removes your Switch accounts on its Switches. You can join again with its Invite link.',
    run: () => projectService.leave(p.id), failed: 'leave the Project',
  }),
  archive: (p) => ({
    title: `Archive ${p.name}?`, label: 'Archive', danger: false,
    message: 'Nobody can reserve into it or join it any more. It stays listed under Archived.',
    run: () => projectService.archive(p.id), done: `${p.name} archived.`, failed: 'archive the Project',
  }),
};

const ask = (action, ...args) => { confirm.value = ACTIONS[action](...args); };

const runConfirm = async () => {
  const action = confirm.value;
  pending.value = true;
  const result = await action.run();
  pending.value = false;
  confirm.value = null;
  if (result.success) toast.success(action.done || result.data.detail);
  else toast.error(`Couldn't ${action.failed}. ${result.message}`);
  refresh();
};
</script>
