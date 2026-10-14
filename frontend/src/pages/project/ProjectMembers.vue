<template>
  <!-- The Members section of a Project's page (CONTEXT.md): who may work on its Switches. The
       owner shares and renews the Invite link and removes members; any other member may leave.
       An archived Project only lists them. -->
  <div class="container mx-auto max-w-5xl px-4 py-6">
    <h2 class="sr-only">Members</h2>
    <p class="mb-4 text-sm text-gray-600">
      Every member may work on all the Project's Switches and gets a Switch account on each.
      <template v-if="project.is_owner && !project.archived_at">Invite people with the Invite link.</template>
    </p>

    <!-- The Invite link: the owner's to share and renew -->
    <div v-if="project.is_owner && project.invite_token" class="mb-4 flex flex-wrap items-center gap-2 rounded-lg bg-white p-4 shadow-sm ring-1 ring-gray-200" data-testid="invite-link">
      <label for="invite-link" class="text-sm font-medium text-gray-700">Invite link</label>
      <input id="invite-link" :value="inviteUrl(project.invite_token)" readonly class="input w-auto flex-1 font-mono text-xs" @focus="$event.target.select()" />
      <UiButton size="sm" @click="copy">Copy</UiButton>
      <UiButton size="sm" @click="ask('renew')">Renew link</UiButton>
    </div>

    <ul class="divide-y divide-gray-100 rounded-lg bg-white text-sm shadow-sm ring-1 ring-gray-200" aria-label="Members">
      <li v-for="m in project.members" :key="m.id" :data-member="m.username" class="flex items-center justify-between px-4 py-2">
        <span>
          {{ m.username }}
          <UiBadge v-if="m.id === project.owner?.id" class="ml-1">Owner</UiBadge>
          <UiBadge v-else-if="isMe(m.id)" class="ml-1">You</UiBadge>
        </span>
        <template v-if="!project.archived_at">
          <UiButton v-if="project.is_owner && m.id !== project.owner?.id" size="sm" @click="ask('remove', m)">Remove</UiButton>
          <UiButton v-else-if="isMe(m.id) && !project.is_owner" size="sm" variant="danger" @click="ask('leave')">Leave</UiButton>
        </template>
      </li>
    </ul>

    <UiConfirm
      v-if="confirm" :title="confirm.title" :message="confirm.message" :confirm-label="confirm.label"
      :danger="confirm.danger" :pending="pending" @close="confirm = null" @confirm="runConfirm"
    />
  </div>
</template>

<script setup>
import { ref } from 'vue';
import { useRouter } from 'vue-router';
import UiBadge from '../../components/ui/UiBadge.vue';
import UiButton from '../../components/ui/UiButton.vue';
import UiConfirm from '../../components/ui/UiConfirm.vue';
import { projectService } from '../../utils/apiService.js';
import { inviteUrl } from '../../utils/projects.js';
import { isMe } from '../../auth.js';
import { toast } from '../../composables/toast.js';

const props = defineProps({
  project: { type: Object, required: true },  // as projects/<id>/ gives it (Project.vue)
});
// changed: the members or the Invite link changed: the page reloads the Project
const emit = defineEmits(['changed']);
const router = useRouter();

const copy = async () => {
  try {
    await navigator.clipboard.writeText(inviteUrl(props.project.invite_token));
    toast.success('Invite link copied.');
  } catch {
    toast.error('Couldn\'t copy: select the link and copy it yourself.');
  }
};

// --- Renew the link, remove, leave: each asks first ---
const confirm = ref(null);
const pending = ref(false);
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
    then: () => router.push('/projects'),  // no longer ours to see
  }),
};

const ask = (action, ...args) => { confirm.value = ACTIONS[action](props.project, ...args); };

const runConfirm = async () => {
  const action = confirm.value;
  pending.value = true;
  const result = await action.run();
  pending.value = false;
  confirm.value = null;
  if (!result.success) {
    toast.error(`Couldn't ${action.failed}. ${result.message}`);
    return;
  }
  toast.success(action.done || result.data.detail);
  if (action.then) action.then();
  else emit('changed');
};
</script>
