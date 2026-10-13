<template>
  <!-- An Invite link (CONTEXT.md): what the Project is, then Join. Logged out, the visitor logs in
       first and comes back here (router.js). -->
  <div class="container mx-auto max-w-xl px-4 py-12">
    <div v-if="loading" class="flex justify-center py-16 text-primary-700"><UiSpinner size="lg" /></div>

    <div v-else-if="problem" class="rounded-lg bg-white p-6 text-center shadow-sm ring-1 ring-gray-200" data-testid="join-problem">
      <h1 class="text-xl font-semibold text-gray-900">This Invite link doesn't work</h1>
      <p class="mt-2 text-sm text-gray-600">{{ problem }}</p>
      <UiButton to="/" class="mt-6">Go to My lab</UiButton>
    </div>

    <section v-else class="rounded-lg bg-white p-6 shadow-sm ring-1 ring-gray-200" aria-labelledby="join-heading">
      <p class="text-sm text-gray-600">You are invited to the Project</p>
      <h1 id="join-heading" class="mt-1 text-2xl font-bold text-gray-900">{{ project.name }}</h1>
      <p v-if="project.reference" class="text-sm text-gray-600">{{ project.reference }}</p>
      <dl class="mt-4 grid grid-cols-[auto,1fr] gap-x-4 gap-y-1 text-sm">
        <dt class="text-gray-500">Owner</dt>
        <dd class="text-gray-900" data-testid="join-owner">{{ project.owner || 'nobody' }}</dd>
        <dt class="text-gray-500">Members</dt>
        <dd class="text-gray-900">{{ project.members }}</dd>
        <dt class="text-gray-500">Switches</dt>
        <dd class="text-gray-900" data-testid="join-switches">
          <template v-if="project.switches.length">{{ project.switches.map(switchLabel).join(', ') }}</template>
          <template v-else>None reserved right now</template>
        </dd>
      </dl>

      <template v-if="member">
        <p class="mt-6 text-sm text-success-700">You are a member of this Project.</p>
        <div class="mt-4 flex gap-2">
          <UiButton variant="primary" :to="{ path: '/topology', query: { project: project.id } }">Open its Topology</UiButton>
          <UiButton to="/projects">My Projects</UiButton>
        </div>
      </template>
      <template v-else>
        <p class="mt-6 text-sm text-gray-700">
          As a member you may work on every Switch of the Project: Renew, Release, Connect and Disconnect.
          BLab gives you a Switch account on each of them, which opens with your Switch password.
          You may leave the Project at any time.
        </p>
        <div class="mt-4 flex gap-2">
          <UiButton variant="primary" :pending="joining" @click="join">Join {{ project.name }}</UiButton>
          <UiButton to="/">Not now</UiButton>
        </div>
      </template>
    </section>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';
import UiButton from '../components/ui/UiButton.vue';
import UiSpinner from '../components/ui/UiSpinner.vue';
import { projectService } from '../utils/apiService.js';
import { switchLabel } from '../utils/switchLabel.js';
import { toast } from '../composables/toast.js';

const route = useRoute();
const loading = ref(true);
const problem = ref('');
const project = ref(null);
const member = ref(false);
const joining = ref(false);

const show = (data) => {
  project.value = data.project;
  member.value = data.member;
};

onMounted(async () => {
  const result = await projectService.preview(route.params.token);
  loading.value = false;
  if (result.success) show(result.data);
  else problem.value = result.status === 404
    ? (result.message || 'It may have been renewed, or its Project archived. Ask the owner for the current one.')
    : `Couldn't open it. ${result.message}`;
});

const join = async () => {
  joining.value = true;
  const result = await projectService.join(route.params.token);
  joining.value = false;
  if (!result.success) {
    toast.error(`Couldn't join the Project. ${result.message}`);
    return;
  }
  show(result.data);
  toast.success(result.data.detail);
};
</script>
