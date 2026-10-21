<template>
  <!-- The Switches section of a Project's page: the Switches reserved into it, who holds each
       and until when. Any member may Renew or Release them. A Switch belongs to a Project only
       through its Reservation, so adding one means reserving it into the Project. -->
  <div class="container mx-auto max-w-5xl px-4 py-6">
    <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
      <h2 class="text-lg font-semibold text-gray-900">Switches</h2>
      <UiButton v-if="!project.archived_at" variant="primary" :to="reserveIntoPath(project.id)">{{ RESERVE_INTO_LABEL }}</UiButton>
    </div>

    <div v-if="!loaded" class="flex justify-center py-16 text-primary-700"><UiSpinner size="lg" /></div>
    <p v-else-if="!switches.length" class="rounded-lg bg-white p-8 text-center text-gray-600 shadow-sm ring-1 ring-gray-200" data-testid="no-switches">
      {{ project.archived_at ? 'This Project is archived: it holds no Switch.' : 'No Switch in this Project yet.' }}
    </p>
    <ul v-else class="divide-y divide-gray-200 rounded-lg bg-white shadow-sm ring-1 ring-gray-200" aria-label="Switches of the Project">
      <li v-for="s in switches" :key="s.id" :data-switch="s.id" class="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div class="font-medium text-gray-900">{{ s.model }} <span class="font-normal text-gray-500">{{ s.mngt_IP }}</span></div>
          <div class="text-sm text-gray-600">
            Reserved by <span data-testid="holder">{{ holderName(s.reservation) }}</span>,
            until {{ formatDate(s.reservation.end_date, { relative: true }) }}
            <UiBadge v-if="s.reservation.admin_exception" class="ml-1">Admin exception</UiBadge>
          </div>
          <div class="text-sm text-gray-600">
            {{ plural(s.links, 'Link', 'Links') }} on {{ plural(s.ports, 'cabled port', 'cabled ports') }}<template v-if="!whyNoRenewal(s.reservation)">
              · {{ plural(s.reservation.renewals_left, 'Renewal', 'Renewals') }} left</template>
          </div>
        </div>
        <div class="flex flex-wrap items-center gap-2 sm:justify-end">
          <UiButton
            v-if="!whyNoRenewal(s.reservation)" size="sm" variant="primary" :pending="renewing === s.id"
            :title="`Push the end date back by ${RENEWAL_DAYS} days`" @click="renew(s)"
          >Renew</UiButton>
          <span v-else class="text-xs text-gray-500">{{ whyNoRenewal(s.reservation) }}</span>
          <UiButton size="sm" title="Into another of your Projects, without a Release" @click="switchToMove = s">Move…</UiButton>
          <UiButton size="sm" variant="danger" @click="switchToRelease = s.id">Release</UiButton>
        </div>
      </li>
    </ul>

    <ReleaseDialog v-if="switchToRelease !== null" :switchId="switchToRelease" @released="tookOut" @close="switchToRelease = null" />
    <MoveDialog
      v-if="switchToMove" :switch-id="switchToMove.id" :label="switchLabel(switchToMove)" :from-project="project.id"
      @moved="tookOut" @close="switchToMove = null"
    />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue';
import MoveDialog from '../../components/MoveDialog.vue';
import ReleaseDialog from '../../components/ReleaseDialog.vue';
import UiBadge from '../../components/ui/UiBadge.vue';
import UiButton from '../../components/ui/UiButton.vue';
import UiSpinner from '../../components/ui/UiSpinner.vue';
import { projectService, switchService } from '../../utils/apiService.js';
import { RENEWAL_DAYS } from '../../utils/constants.js';
import { formatDate } from '../../utils/dateUtils.js';
import { plural } from '../../utils/plural.js';
import { RESERVE_INTO_LABEL, reserveIntoPath } from '../../utils/projects.js';
import { whyNoRenewal } from '../../utils/renewal.js';
import { switchLabel } from '../../utils/switchLabel.js';
import { toast } from '../../composables/toast.js';
import { isUnreachable, usePoll } from '../../composables/poll.js';

const props = defineProps({
  project: { type: Object, required: true },  // as projects/<id>/ gives it (Project.vue)
});
// changed: a Release or a Move took a Switch out of the Project: the page counts them again
const emit = defineEmits(['changed']);

const loaded = ref(false);
const topology = ref(null);  // projects/<id>/topology/: its Switches come with their Reservation and Ports
const renewing = ref(null);  // the id of the Switch being Renewed
const switchToRelease = ref(null);
const switchToMove = ref(null);  // the Switch whose Move dialog is open

// The Project's own Switches (a Link may draw in one outside it), with their Ports and Links
const switches = computed(() => {
  if (!topology.value) return [];
  const ports = topology.value.ports;
  return topology.value.switches.filter(s => s.in_topology).map(s => {
    const own = ports.filter(p => p.switch === s.id);
    return { ...s, ports: own.length, links: own.filter(p => p.svlan != null).length };
  });
});

// The holder is a member: a member holding a Switch can't leave until it is Released
const holderName = (reservation) => props.project.members.find(m => m.id === reservation.user)?.username || 'a former member';

// Returns the API result, for the poll to tell whether BLab answered
const load = async () => {
  const result = await projectService.topology(props.project.id);
  if (result.success) {
    topology.value = result.data;
    loaded.value = true;
  } else if (result.status === 404) {
    emit('changed');  // no longer ours: the page says so
  } else if (!isUnreachable(result)) {
    toast.error(`Couldn't load the Switches. ${result.message}`, 'project-switches-load');
  }
  return [result];
};
const { refresh } = usePoll(load, 5 * 1000);

const renew = async (s) => {
  renewing.value = s.id;
  const result = await switchService.renew(s.id);
  renewing.value = null;
  if (result.success) toast.success(result.data.detail);
  else toast.error(`Couldn't Renew this Reservation. ${result.message}`);
  refresh();
};

// A Release or a Move took a Switch out of the Project
const tookOut = () => {
  refresh();
  emit('changed');
};
</script>
