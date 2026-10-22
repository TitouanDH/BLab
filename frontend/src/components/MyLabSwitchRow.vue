<template>
  <!-- One Switch on My lab: what working on it needs. Its model and IP (a dot when one of its
       Links is a Ghost Link), the time its Reservation has left, my login on it (my Switch
       account, or where BLab is with it), its Links, Renew and Release. -->
  <li :data-switch="sw.id" class="grid gap-3 p-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1.3fr)_minmax(0,1.5fr)_minmax(0,0.7fr)_9.5rem] lg:items-center lg:gap-4">
    <div>
      <div class="flex items-center gap-2 font-medium text-gray-900">
        {{ sw.model }}
        <span
          v-if="sw.ghost_links" data-ghost-dot class="inline-block h-2.5 w-2.5 shrink-0 rounded-full bg-ghost-600"
          :title="ghostText" role="img" :aria-label="ghostText"
        />
      </div>
      <div class="font-mono text-sm text-gray-600">{{ sw.mngt_IP }}</div>
      <div v-if="!isMe(sw.holder_id)" class="text-xs text-gray-500">Held by {{ sw.holder }}</div>
    </div>

    <!-- Time left, as a bar: full for the longest a Reservation is made for, and beyond -->
    <div class="text-sm text-gray-600">
      <div
        class="mb-1 h-1.5 w-full max-w-[14rem] overflow-hidden rounded-full bg-gray-200" role="progressbar"
        :aria-valuenow="Math.round(timeLeft * 100)" aria-valuemin="0" aria-valuemax="100" aria-label="Time left"
      >
        <div :class="['h-full rounded-full', ending ? 'bg-warning-500' : 'bg-primary-600']" :style="{ width: `${timeLeft * 100}%` }" />
      </div>
      <div>
        Until {{ formatDate(sw.end_date, { relative: true }) }}
        <UiBadge v-if="sw.admin_exception" class="ml-1">Admin exception</UiBadge>
      </div>
      <div v-if="!renewBlocker" class="text-xs">{{ sw.renewals_left }} Renewal{{ sw.renewals_left === 1 ? '' : 's' }} left</div>
      <div v-else class="text-xs text-gray-500">{{ renewBlocker }}</div>
    </div>

    <!-- My login: my Switch account on it -->
    <div class="text-sm" data-login>
      <!-- No account on a Switch without a management IP: its IP already says "Not available" -->
      <template v-if="account?.state === 'ready'">
        <div class="flex flex-wrap items-center gap-2">
          <span class="text-gray-500">Login</span>
          <code class="font-mono text-gray-900">{{ account.name }}</code>
          <UiButton size="sm" :title="sshCommand" @click="copyText(sshCommand, 'SSH command')">Copy SSH command</UiButton>
        </div>
        <!-- A VC slave refuses local users: the account is the one made on its master, for the whole VC -->
        <div v-if="account.vc_master" class="mt-1 text-xs text-gray-500">Made on its VC master, {{ account.vc_master }}</div>
        <!-- Before the Switch password (Projects off): BLab's random password, for my eyes only -->
        <div v-if="account.password" class="mt-1 flex flex-wrap items-center gap-2">
          <span class="text-gray-500">Password</span>
          <span class="font-mono" data-password>{{ shown ? account.password : '••••••••••••' }}</span>
          <UiButton size="sm" @click="shown = !shown">{{ shown ? 'Hide' : 'Show' }}</UiButton>
          <UiButton size="sm" @click="copyPassword">Copy</UiButton>
        </div>
      </template>
      <span v-else-if="account?.state === 'pending'" class="text-gray-500">Your Switch account is being created...</span>
      <span v-else-if="account" class="text-warning-700">Your Switch account isn't created yet, BLab keeps trying: {{ account.error }}</span>
    </div>

    <div class="text-sm text-gray-700" data-links>
      {{ plural(sw.links, 'Link', 'Links') }}
      <div v-if="sw.ghost_links" class="mt-0.5"><UiBadge tone="ghost">{{ plural(sw.ghost_links, 'Ghost Link', 'Ghost Links') }}</UiBadge></div>
    </div>

    <div class="flex flex-wrap items-center gap-2 lg:justify-end">
      <UiButton
        v-if="!renewBlocker" size="sm" variant="primary" :pending="renewing"
        :title="`Push the end date back by ${RENEWAL_DAYS} days`" @click="$emit('renew', sw)"
      >
        Renew
      </UiButton>
      <UiButton size="sm" :to="`/switches/${sw.id}/config`" title="Its live config, Golden init and Snapshots, read-only">Config</UiButton>
      <UiButton v-if="movable" size="sm" title="Into another of your Projects, without a Release" @click="$emit('move', sw)">Move…</UiButton>
      <UiButton size="sm" variant="danger" @click="$emit('release', sw)">Release</UiButton>
    </div>
  </li>
</template>

<script setup>
import { computed, ref } from 'vue';
import UiBadge from './ui/UiBadge.vue';
import UiButton from './ui/UiButton.vue';
import { formatDate } from '../utils/dateUtils.js';
import { plural } from '../utils/plural.js';
import { MAX_RESERVATION_DAYS, RENEWAL_DAYS } from '../utils/constants.js';
import { whyNoRenewal } from '../utils/renewal.js';
import { isMe } from '../auth.js';
import { copyText } from '../utils/clipboard.js';

const props = defineProps({
  // A Switch as my_lab/ gives it: its Reservation, Links, Ghost Links and my Switch account
  sw: { type: Object, required: true },
  renewing: Boolean,
  movable: Boolean,  // in a Project, with Projects on: it may move to another
});
defineEmits(['renew', 'release', 'move']);

const DAY_MS = 24 * 60 * 60 * 1000;
const shown = ref(false);
const account = computed(() => props.sw.account);
const sshCommand = computed(() => `ssh ${account.value?.name}@${props.sw.mngt_IP}`);
const ghostText = computed(() => `${plural(props.sw.ghost_links, 'Ghost Link', 'Ghost Links')}: `
  + 'a Link of this Switch carries no traffic. Its Topology says why.');

// Time left on one scale for every row, the longest a Reservation is made for
// (MAX_RESERVATION_DAYS): it only shrinks as time passes and grows at a Renewal, whenever the
// Reservation was made. Beyond that scale (Renewed, or an admin exception) it stays full.
const timeLeft = computed(() => {
  const left = new Date(props.sw.end_date).getTime() - Date.now();
  return Number.isFinite(left) ? Math.min(1, Math.max(0, left / (MAX_RESERVATION_DAYS * DAY_MS))) : 0;
});
const ending = computed(() => new Date(props.sw.end_date).getTime() - Date.now() < DAY_MS);

// Why Renew isn't offered, in the words of the Renewal rules
const renewBlocker = computed(() => whyNoRenewal(props.sw));

// A password is never put in a toast: where it can't be copied, it is shown on the row instead
const copyPassword = async () => {
  if (!(await copyText(account.value.password, 'Password', { reveal: false }))) shown.value = true;
};
</script>
