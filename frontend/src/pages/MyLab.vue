<template>
  <!-- My lab: the first page after logging in. My Reservations (Renew, Release), the
       Quarantines naming me, my Topology in miniature, and the Topologies shared with me. -->
  <div class="container mx-auto max-w-5xl px-4 py-8">
    <h1 class="mb-6 text-2xl font-bold text-gray-900">My lab</h1>

    <div v-if="!loaded" class="flex justify-center py-16 text-primary-700"><UiSpinner size="lg" /></div>

    <template v-else>
      <!-- Quarantines naming me: amber, and first, because they block new Reservations -->
      <section
        v-if="myQuarantines.length"
        class="mb-8 rounded-lg border border-warning-300 bg-warning-50 p-4 text-warning-900"
        aria-labelledby="quarantine-heading"
        data-testid="quarantine-notice"
      >
        <h2 id="quarantine-heading" class="text-lg font-semibold">
          {{ myQuarantines.length === 1 ? 'A Quarantine names you' : `${myQuarantines.length} Quarantines name you` }}
        </h2>
        <p class="mt-1 text-sm">
          While a Quarantine names you, you can't make new Reservations. Fix what the Inspection found
          on the Switch, then press Re-check: BLab Inspects it again and lifts the Quarantine once it is clean.
        </p>
        <ul class="mt-4 space-y-3">
          <li v-for="s in myQuarantines" :key="s.id" :data-switch="s.mngt_IP" class="rounded-md bg-white p-3 ring-1 ring-warning-200">
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div class="font-medium text-gray-900">{{ s.model }} <span class="font-normal text-gray-500">{{ s.mngt_IP }}</span></div>
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

      <!-- My Reservations -->
      <section class="mb-8" aria-labelledby="reservations-heading">
        <h2 id="reservations-heading" class="mb-3 text-lg font-semibold text-gray-900">My Reservations</h2>
        <div v-if="!myReservations.length" class="rounded-lg bg-white p-8 text-center shadow-sm ring-1 ring-gray-200" data-testid="empty-state">
          <p class="font-medium text-gray-900">You hold no Reservation.</p>
          <p class="mt-1 text-sm text-gray-600">
            Reserve a Switch to work on it for up to {{ MAX_RESERVATION_DAYS }} days, then wire it to others on your Topology.
          </p>
          <UiButton to="/reservation" variant="primary" class="mt-4">Reserve a Switch</UiButton>
        </div>
        <ul v-else class="divide-y divide-gray-200 rounded-lg bg-white shadow-sm ring-1 ring-gray-200">
          <li v-for="s in myReservations" :key="s.id" :data-switch="s.mngt_IP" class="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div class="font-medium text-gray-900">{{ s.model }} <span class="font-normal text-gray-500">{{ s.mngt_IP }}</span></div>
              <div class="text-sm text-gray-600">
                Until {{ formatDate(s.end_date, { relative: true }) }}
                <UiBadge v-if="s.admin_exception" class="ml-1">Admin exception</UiBadge>
              </div>
              <div v-if="!renewBlocker(s)" class="text-sm text-gray-600">
                {{ s.renewals_left }} Renewal{{ s.renewals_left === 1 ? '' : 's' }} left
              </div>
            </div>
            <div class="flex flex-wrap items-center gap-2 sm:justify-end">
              <UiButton
                v-if="!renewBlocker(s)" size="sm" variant="primary" :pending="renewing === s.id"
                :title="`Push the end date back by ${RENEWAL_DAYS} days`" @click="renew(s)"
              >
                Renew
              </UiButton>
              <span v-else class="text-xs text-gray-500">{{ renewBlocker(s) }}</span>
              <UiButton size="sm" variant="danger" @click="switchToRelease = s.id">Release</UiButton>
            </div>
          </li>
        </ul>
      </section>

      <!-- My Topology in miniature -->
      <section v-if="myReservations.length" class="mb-8" aria-labelledby="topology-heading">
        <div class="mb-3 flex items-baseline justify-between">
          <h2 id="topology-heading" class="text-lg font-semibold text-gray-900">My Topology</h2>
          <router-link to="/topology" class="text-sm font-semibold text-primary-700 hover:text-primary-800">Open the Topology <span aria-hidden="true">&rarr;</span></router-link>
        </div>
        <TopologyMiniature v-if="topology" :topology="topology" to="/topology" />
        <p v-else class="rounded-lg bg-white p-6 text-center text-sm text-gray-500 shadow-sm ring-1 ring-gray-200">Couldn't load your Topology.</p>
      </section>

      <!-- Shared with me -->
      <section aria-labelledby="shared-heading">
        <h2 id="shared-heading" class="mb-3 text-lg font-semibold text-gray-900">Shared with me</h2>
        <ul v-if="sharedWithMe.length" class="divide-y divide-gray-200 rounded-lg bg-white shadow-sm ring-1 ring-gray-200">
          <li v-for="share in sharedWithMe" :key="share.id">
            <router-link
              :to="{ path: '/topology', query: { owner: share.owner_id } }"
              class="flex items-center justify-between p-4 hover:bg-gray-50"
            >
              <span class="font-medium text-gray-900">{{ share.owner_username }}'s Topology</span>
              <span class="text-sm font-semibold text-primary-700">Open <span aria-hidden="true">&rarr;</span></span>
            </router-link>
          </li>
        </ul>
        <p v-else class="text-sm text-gray-500">No Topology is shared with you.</p>
      </section>
    </template>

    <ReleaseDialog v-if="switchToRelease !== null" :switchId="switchToRelease" @released="load" @close="switchToRelease = null" />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue';
import ReleaseDialog from '../components/ReleaseDialog.vue';
import TopologyMiniature from '../components/TopologyMiniature.vue';
import UiBadge from '../components/ui/UiBadge.vue';
import UiButton from '../components/ui/UiButton.vue';
import UiSpinner from '../components/ui/UiSpinner.vue';
import { labStatusService, switchService, topologyService } from '../utils/apiService.js';
import { formatDate } from '../utils/dateUtils.js';
import { MAX_RENEWALS, MAX_RESERVATION_DAYS, RENEWAL_DAYS } from '../utils/constants.js';
import { getCurrentUserId, isMe } from '../auth.js';
import { toast } from '../composables/toast.js';
import { isUnreachable, usePoll } from '../composables/poll.js';

const myId = getCurrentUserId();
const loaded = ref(false);
const lab = ref([]);            // lab_status rows: every Switch, its holder, its Quarantine
const topology = ref(null);     // my Topology, as topology/<my id>/ answers it
const sharedWithMe = ref([]);
const renewing = ref(null);     // the id of the Switch being Renewed
const rechecking = ref(null);   // the id of the Switch being Re-checked
const switchToRelease = ref(null);

const myReservations = computed(() => lab.value.filter(s => isMe(s.holder_id)));
const myQuarantines = computed(() => lab.value.filter(s => s.quarantine && isMe(s.quarantine.holder_id)));

// Returns the API results, for the poll to tell whether BLab answered
const load = async () => {
  const [status, mine, shared] = await Promise.all([
    labStatusService.get(),
    topologyService.get(myId),
    topologyService.getShared(),
  ]);
  if (status.success) lab.value = status.data.switches;
  if (mine.success) topology.value = mine.data;
  if (shared.success) sharedWithMe.value = shared.data.shared_with_me || [];
  if (status.success) loaded.value = true;
  return report([status, mine, shared]);
};

// BLab refusing (rather than not answering) is said once, in an error toast
const report = (results) => {
  const refused = results.find(r => !r.success && !isUnreachable(r));
  if (refused) toast.error(`Couldn't load My lab. ${refused.message}`, 'my-lab-load');
  return results;
};

// Why Renew isn't offered, in the words of the Renewal rules (api/api/reservations.py)
const renewBlocker = (s) => {
  if (s.admin_exception) return 'An admin set this end date: ask an admin to change it.';
  if (s.end_date && new Date(s.end_date) <= new Date()) return 'Expired: it is being Released.';
  if (!s.renewals_left) return `No Renewals left: it has been Renewed ${MAX_RENEWALS} times.`;
  return null;
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
    toast.error(`Couldn't Re-check ${s.mngt_IP}. ${result.message}`);
  } else if (result.data.clean) {
    toast.success(result.data.detail);
  } else {
    toast.error(result.data.detail);  // still not clean: stays until read
  }
  await load();
};

usePoll(load, 5 * 1000);
</script>
