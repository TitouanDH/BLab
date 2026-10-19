<template>
  <!-- My lab: the first page after logging in. The Quarantines naming me, then every Switch I may
       work on, grouped by Project (with Projects off: my Reservations, then each Topology shared
       with me), each with my login on it, Renew and Release. -->
  <div class="container mx-auto max-w-6xl px-4 py-8">
    <h1 class="mb-6 text-2xl font-bold text-gray-900">My lab</h1>

    <div v-if="!loaded" class="flex justify-center py-16 text-primary-700"><UiSpinner size="lg" /></div>

    <template v-else>
      <!-- Quarantines naming me: amber, and first, because they block new Reservations -->
      <section
        v-if="quarantines.length"
        class="mb-8 rounded-lg border border-warning-300 bg-warning-50 p-4 text-warning-900"
        aria-labelledby="quarantine-heading"
        data-testid="quarantine-notice"
      >
        <h2 id="quarantine-heading" class="text-lg font-semibold">
          {{ quarantines.length === 1 ? 'A Quarantine names you' : `${quarantines.length} Quarantines name you` }}
        </h2>
        <p class="mt-1 text-sm">
          While a Quarantine names you, you can't make new Reservations. Fix what the Inspection found
          on the Switch, then press Re-check: BLab Inspects it again and lifts the Quarantine once it is clean.
        </p>
        <ul class="mt-4 space-y-3">
          <li v-for="s in quarantines" :key="s.id" :data-switch="s.id" class="rounded-md bg-white p-3 ring-1 ring-warning-200">
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div class="font-medium text-gray-900">{{ switchLabel(s) }} <span class="font-normal text-gray-500">{{ s.mngt_IP }}</span></div>
                <div class="text-xs text-gray-600">In Quarantine since {{ formatDate(s.quarantine.opened_at) }}</div>
                <ul class="mt-2 list-disc pl-5 text-sm text-gray-800">
                  <li v-for="reason in s.quarantine.reasons" :key="reason">{{ reason }}</li>
                </ul>
              </div>
              <UiButton v-if="!s.cleaning_up" size="sm" variant="primary" :pending="rechecking === s.id" @click="recheck(s)">Re-check</UiButton>
              <UiBadge v-else tone="primary">Being Cleaned up</UiBadge>
            </div>
          </li>
        </ul>
      </section>

      <!-- How to log in, once for the whole page (the Topology's "My Switch accounts" leads here) -->
      <p id="switch-accounts" class="mb-6 text-sm text-gray-600">
        <template v-if="!switchCount">Once you may work on a Switch, its row here gives your login on it.</template>
        Log in to a Switch by SSH with the login on its row
        <template v-if="usesSwitchPassword">
          and your Switch password, which you can change on your
          <router-link to="/account" class="text-primary-700 underline">Account</router-link> page.
        </template>
        <template v-else>and the password shown there.</template>
        BLab creates your Switch accounts on every Switch you may work on, and removes them at Release{{ projectsOn ? ' or when you leave its Project' : '' }}.
        The <code>admin</code> login is BLab's only.
      </p>

      <div v-if="!groups.length" class="rounded-lg bg-white p-8 text-center shadow-sm ring-1 ring-gray-200" data-testid="empty-state">
        <p class="font-medium text-gray-900">You are in no Project yet.</p>
        <p class="mt-1 text-sm text-gray-600">Your first Reservation makes one, and you can invite others into it.</p>
        <UiButton to="/reservation" variant="primary" class="mt-4">Reserve a Switch</UiButton>
      </div>

      <section
        v-for="g in groups" :key="g.key" class="mb-8" :aria-labelledby="`group-${g.key}`"
        :data-group="g.key" data-testid="lab-group"
      >
        <div class="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <div>
            <h2 :id="`group-${g.key}`" class="text-lg font-semibold text-gray-900">
              {{ g.title }}
              <span v-if="g.project?.reference" class="ml-1 text-sm font-normal text-gray-500">{{ g.project.reference }}</span>
            </h2>
            <p v-if="g.project" class="text-sm text-gray-600" data-members>
              {{ plural(g.project.members.length, 'member', 'members') }}:
              <template v-for="(m, i) in g.project.members" :key="m.id">{{ i ? ', ' : '' }}{{ m.username }}<span v-if="m.id === g.project.owner?.id" class="text-gray-500"> (owner)</span></template>
            </p>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <UiButton v-if="g.project?.invite_token" size="sm" :title="inviteUrl(g.project.invite_token)" @click="copyInvite(g.project)">Copy Invite link</UiButton>
            <UiButton v-if="g.topology" size="sm" :to="g.topology">Open Topology <span aria-hidden="true">&rarr;</span></UiButton>
          </div>
        </div>

        <ul v-if="g.switches.length" class="divide-y divide-gray-200 rounded-lg bg-white shadow-sm ring-1 ring-gray-200">
          <MyLabSwitchRow
            v-for="s in g.switches" :key="s.id" :sw="s" :renewing="renewing === s.id" :movable="!!g.project"
            @renew="renew" @release="switchToRelease = $event.id" @move="switchToMove = { sw: $event, from: g.project.id }"
          />
        </ul>
        <div v-else-if="g.mine" class="rounded-lg bg-white p-8 text-center shadow-sm ring-1 ring-gray-200" data-testid="empty-state">
          <p class="font-medium text-gray-900">You hold no Reservation.</p>
          <p class="mt-1 text-sm text-gray-600">
            Reserve a Switch to work on it for up to {{ MAX_RESERVATION_DAYS }} days, then wire it to others on your Topology.
          </p>
          <UiButton to="/reservation" variant="primary" class="mt-4">Reserve a Switch</UiButton>
        </div>
        <div v-else class="rounded-lg bg-white p-4 text-sm text-gray-500 shadow-sm ring-1 ring-gray-200">
          <template v-if="g.project">
            No Switch is reserved into {{ g.project.name }}.
            <router-link :to="reserveIntoPath(g.project.id)" class="font-semibold text-primary-700 hover:text-primary-800">{{ RESERVE_INTO_LABEL }}</router-link>
          </template>
          <template v-else>{{ g.holder.username }} holds no Switch now.</template>
        </div>
      </section>
    </template>

    <ReleaseDialog v-if="switchToRelease !== null" :switchId="switchToRelease" @released="load" @close="switchToRelease = null" />
    <MoveDialog
      v-if="switchToMove" :switch-id="switchToMove.sw.id" :label="switchLabel(switchToMove.sw)" :from-project="switchToMove.from"
      @moved="load" @close="switchToMove = null"
    />
  </div>
</template>

<script setup>
import { computed, nextTick, ref } from 'vue';
import { useRoute } from 'vue-router';
import MyLabSwitchRow from '../components/MyLabSwitchRow.vue';
import MoveDialog from '../components/MoveDialog.vue';
import ReleaseDialog from '../components/ReleaseDialog.vue';
import UiBadge from '../components/ui/UiBadge.vue';
import UiButton from '../components/ui/UiButton.vue';
import UiSpinner from '../components/ui/UiSpinner.vue';
import { labStatusService, myLabService, switchService } from '../utils/apiService.js';
import {
  RESERVE_INTO_LABEL, inviteUrl, projectTopologyPath, rememberProjects, reserveIntoPath, userTopologyPath,
} from '../utils/projects.js';
import { formatDate } from '../utils/dateUtils.js';
import { plural } from '../utils/plural.js';
import { switchLabel } from '../utils/switchLabel.js';
import { MAX_RESERVATION_DAYS } from '../utils/constants.js';
import { isMe } from '../auth.js';
import { toast } from '../composables/toast.js';
import { copyText } from '../utils/clipboard.js';
import { isUnreachable, usePoll } from '../composables/poll.js';

const loaded = ref(false);
const lab = ref({ projects: false, groups: [], quarantines: [] });  // the my_lab/ answer
const renewing = ref(null);     // the id of the Switch being Renewed
const rechecking = ref(null);   // the id of the Switch being Re-checked
const switchToRelease = ref(null);
const switchToMove = ref(null);  // { sw, from }: the Switch whose Move dialog is open, and its Project
const route = useRoute();

const projectsOn = computed(() => lab.value.projects);
const quarantines = computed(() => lab.value.quarantines);

// Each group as shown: its title, and where its Topology opens. With Projects on, a group of a
// holder's is Reservations outside any Project, and BLab opens a user's Topology to that user
// alone (topology.may_see): another holder's has no Topology to open.
const groups = computed(() => lab.value.groups.map(g => {
  if (g.kind === 'project') {
    return { ...g, key: `p${g.project.id}`, title: g.project.name, topology: projectTopologyPath(g.project.id) };
  }
  const mine = isMe(g.holder.id);
  return {
    ...g, key: `u${g.holder.id}`, mine,
    title: mine ? (projectsOn.value ? 'My Reservations outside a Project' : 'My Reservations') : `${g.holder.username}'s Topology`,
    topology: mine || !projectsOn.value ? userTopologyPath(g.holder.id) : null,
  };
}));
const switchCount = computed(() => lab.value.groups.reduce((n, g) => n + g.switches.length, 0));
const usesSwitchPassword = computed(() => lab.value.groups.some(g => g.switches.some(s => s.account?.switch_password)));

// Returns the API result, for the poll to tell whether BLab answered
const load = async () => {
  const result = await myLabService.get();
  if (result.success) {
    lab.value = result.data;
    rememberProjects(result.data.projects);  // the navbar's Projects follows
    if (!loaded.value) {
      loaded.value = true;
      // /#switch-accounts (the Topology's "My Switch accounts"): there once it is drawn
      if (route.hash === '#switch-accounts') nextTick(() => document.getElementById('switch-accounts')?.scrollIntoView());
    }
  } else if (!isUnreachable(result)) {
    // BLab refusing (rather than not answering) is said once, in an error toast
    toast.error(`Couldn't load My lab. ${result.message}`, 'my-lab-load');
  }
  return result;
};

const renew = async (s) => {
  renewing.value = s.id;
  const result = await switchService.renew(s.id);
  renewing.value = null;
  if (result.success) {
    toast.success(result.data.detail);
  } else {
    toast.error(`Couldn't Renew this Reservation. ${result.message}`);
  }
  await load();
};

// Re-check: an Inspection that lifts the Quarantine if the Switch is clean
const recheck = async (s) => {
  rechecking.value = s.id;
  const result = await labStatusService.recheck(s.id);
  rechecking.value = null;
  if (!result.success) {
    toast.error(`Couldn't Re-check ${switchLabel(s)}. ${result.message}`);
  } else if (result.data.clean) {
    toast.success(result.data.detail);
  } else {
    toast.error(result.data.detail);  // still not clean: stays until read
  }
  await load();
};

const copyInvite = (project) => copyText(inviteUrl(project.invite_token), 'Invite link');

usePoll(load, 5 * 1000);
</script>
