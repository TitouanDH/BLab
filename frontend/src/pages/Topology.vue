<template>
  <div class="flex min-h-0 flex-1 flex-col bg-white">
    <div class="flex flex-wrap items-center gap-3 border-b border-gray-200 px-4 py-3">
      <!-- Whose Topology is shown -->
      <label for="topology-owner" class="sr-only">Topology shown</label>
      <select id="topology-owner" v-model="selectedTopologyOwnerId" @change="onTopologyViewChange" class="input w-auto">
        <option :value="myUserId">My Topology</option>
        <option v-for="share in topologiesSharedWithMe" :key="share.owner_id" :value="share.owner_id">
          {{ share.owner_username }}'s Topology
        </option>
      </select>
      <UiButton class="ml-auto" :to="{ path: '/reservation', hash: '#switch-accounts' }">My Switch accounts</UiButton>
      <UiButton variant="primary" @click="showSharePopup = true">Share Topology</UiButton>
    </div>

    <div class="flex min-h-0 flex-1">
      <div class="relative min-h-0 flex-1">
        <div ref="cyContainer" class="absolute inset-0" data-testid="topology-canvas"></div>

        <!-- Connect mode -->
        <div
          v-if="connectFrom"
          role="status"
          class="absolute left-1/2 top-3 z-10 flex max-w-[90%] -translate-x-1/2 items-center gap-3 rounded-lg border border-primary-300 bg-primary-50 px-4 py-2 text-sm text-primary-900 shadow"
        >
          <div>
            <p class="font-medium">Connecting {{ connectFrom.fullName }}: click another port, Esc to cancel</p>
            <p v-if="connectHint" class="mt-0.5 text-primary-800">{{ connectHint }}</p>
          </div>
          <UiButton size="sm" @click="cancelConnect">Cancel</UiButton>
        </div>

        <!-- Controls -->
        <div class="absolute right-3 top-3 z-10 flex flex-col gap-1 rounded-lg border border-gray-200 bg-white/95 p-1 shadow-sm">
          <button v-for="control in controls" :key="control.label" type="button" :title="control.label" :aria-label="control.label"
            class="rounded p-1.5 text-gray-700 hover:bg-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-600"
            @click="control.action">
            <component :is="control.icon" class="h-5 w-5" aria-hidden="true" />
          </button>
        </div>

        <!-- Link details on hover -->
        <div
          v-if="hover"
          class="pointer-events-none absolute z-20 max-w-xs rounded bg-gray-900 px-2 py-1 text-xs text-white shadow"
          :style="{ left: `${hover.x + 12}px`, top: `${hover.y + 12}px` }"
        >
          {{ hover.text }}
        </div>

        <div v-if="isLoading" class="absolute inset-0 flex items-center justify-center bg-white/50 text-primary-700">
          <UiSpinner size="lg" />
        </div>
        <div v-else-if="isEmpty" class="absolute inset-0 flex items-center justify-center p-6">
          <div class="max-w-sm text-center text-gray-600">
            <p class="font-medium text-gray-900">No Switch in this Topology.</p>
            <p v-if="String(selectedTopologyOwnerId) === String(myUserId)" class="mt-1">
              Reserve Switches on the <router-link to="/reservation" class="font-medium text-primary-700 underline">Reservation</router-link> page to see them here.
            </p>
          </div>
        </div>
      </div>

      <TopologySidePanel
        :item="selected"
        :link="selectedPortLink"
        :other-end="selectedPortOtherEnd"
        :ends="selectedLinkEnds"
        :quarantine="selectedQuarantine"
        :may-work="mayWork"
        :busy="selectedDisconnectRunning"
        @connect="startConnect"
        @disconnect="askDisconnect"
        @release="(sw) => (switchToReleaseId = sw.switchId)"
        @select="select"
      />
    </div>

    <UiConfirm
      v-if="confirm"
      :title="confirm.title"
      :message="confirm.message"
      :confirm-label="confirm.label"
      :danger="confirm.danger"
      @close="closeConfirm"
      @confirm="runConfirm"
    />

    <!-- Sharing the Topology -->
    <UiModal v-if="showSharePopup" title="Share Topology" width="lg" @close="showSharePopup = false">
      <div class="mb-6">
        <label for="share-target" class="mb-2 block font-semibold text-gray-900">Share my Topology with</label>
        <div class="flex items-center gap-3">
          <select id="share-target" v-model="shareTargetUserId" class="input flex-grow">
            <option disabled value="">Choose a user</option>
            <option v-for="user in availableUsers" :key="user.id" :value="user.id">
              {{ user.username }}
            </option>
          </select>
          <UiButton variant="primary" :disabled="!shareTargetUserId" :pending="sharing" @click="shareTopology">Share</UiButton>
        </div>
      </div>

      <div class="grid grid-cols-1 gap-6 sm:grid-cols-2">
        <div>
          <h4 class="mb-3 border-b border-gray-200 pb-2 font-semibold text-gray-700">Shared with me</h4>
          <ul v-if="topologiesSharedWithMe.length > 0" class="space-y-2">
            <li v-for="share in topologiesSharedWithMe" :key="share.id" class="flex items-center justify-between rounded-lg bg-gray-50 p-3">
              <span>{{ share.owner_username }}</span>
              <UiButton size="sm" @click="unshareTopology(share.id)">Remove</UiButton>
            </li>
          </ul>
          <p v-else class="text-gray-500">No Topology is shared with you.</p>
        </div>
        <div>
          <h4 class="mb-3 border-b border-gray-200 pb-2 font-semibold text-gray-700">Shared with others</h4>
          <ul v-if="topologiesIShared.length > 0" class="space-y-2">
            <li v-for="share in topologiesIShared" :key="share.id" class="flex items-center justify-between rounded-lg bg-gray-50 p-3">
              <span>{{ share.target_username }}</span>
              <UiButton size="sm" @click="unshareTopology(share.id)">Stop sharing</UiButton>
            </li>
          </ul>
          <p v-else class="text-gray-500">You haven't shared your Topology with anyone.</p>
        </div>
      </div>
    </UiModal>
    <ReleaseDialog v-if="switchToReleaseId !== null" :switchId="switchToReleaseId" @released="updateTopology" @close="switchToReleaseId = null" />
  </div>
</template>

<script setup>
// The Topology canvas (see CONTEXT.md): click to select a Switch, a port or a Link; the side
// panel shows its details and actions. Connect is an explicit mode: Connect on a port, then
// click another port. What is drawn comes from topology/model.js; topology/canvas.js keeps
// cytoscape in step with it on every 2 s refresh without moving anything.
import { computed, markRaw, onMounted, onUnmounted, ref, shallowRef } from 'vue';
import { ArrowPathIcon, ArrowsPointingInIcon, MagnifyingGlassMinusIcon, MagnifyingGlassPlusIcon } from '@heroicons/vue/24/outline';
import { useRoute } from 'vue-router';
import ReleaseDialog from '../components/ReleaseDialog.vue';
import TopologySidePanel from '../components/topology/TopologySidePanel.vue';
import UiButton from '../components/ui/UiButton.vue';
import UiConfirm from '../components/ui/UiConfirm.vue';
import UiModal from '../components/ui/UiModal.vue';
import UiSpinner from '../components/ui/UiSpinner.vue';
import { createCanvas } from '../topology/canvas.js';
import { layoutStore } from '../topology/layoutStore.js';
import { buildElements, linkEdgeId, whyNoConnect, whyNoDisconnect } from '../topology/model.js';
import { labStatusService, portService, userService, topologyService } from '../utils/apiService.js';
import { plainMessage } from '../utils/errorHandler.js';
import { getCurrentUserId } from '../auth.js';
import { toast } from '../composables/toast.js';
import { usePoll } from '../composables/poll.js';

// --- State ---
const cyContainer = ref(null);
const isLoading = ref(true);
const isEmpty = ref(false);
const mayWork = ref(false); // whether we may connect, disconnect and release in the Topology shown
const confirm = ref(null);  // { title, message, label, danger, action } while asking
const switchToReleaseId = ref(null);
let canvas = null;

// What the canvas draws besides the API's answer
let topology = null;              // the last answer of topology/<owner>/
let savedPositions = {};          // Switch positions of the Topology shown (layoutStore)
let fitPending = true;            // fit the view once the Topology shown is first drawn
const labStatus = shallowRef({ quarantines: new Map(), holders: {} });
const connecting = ref(null);     // { portA, portB } while a connect request runs
const disconnecting = new Map();  // svlan -> what was drawn, while the Link worker tears it down
const inFlight = new Set();       // SVLANs whose disconnect request is running
const disconnectRequests = ref(new Set());  // the same, for the Disconnect buttons to show it

// Selection, connect mode and hover. The panel follows `redraws`, counted on every redraw.
const redraws = ref(0);
const selectedId = ref(null);
const connectFrom = ref(null);    // the port data Connect started from
const connectHint = ref('');
const hover = ref(null);          // { text, x, y }
const route = useRoute();

// Sharing state
const showSharePopup = ref(false);
const topologiesSharedWithMe = ref([]);
const topologiesIShared = ref([]);
const users = ref([]);
const shareTargetUserId = ref('');
const sharing = ref(false);
const selectedTopologyOwnerId = ref('');
const myUserId = ref('');

const availableUsers = computed(() => users.value.filter(u => String(u.id) !== String(myUserId.value)));

// --- Init ---
onMounted(async () => {
  myUserId.value = getCurrentUserId() || '';
  // ?owner=<id> opens a Topology shared with me (links from My lab)
  selectedTopologyOwnerId.value = route.query.owner || myUserId.value;
  canvas = markRaw(createCanvas(cyContainer.value, {
    onTap,
    onHover: (h) => { hover.value = h && { text: linkTitle(h.data), x: h.x, y: h.y }; },
    onSwitchesMoved: (positions) => {
      savedPositions = positions;
      if (mayWork.value) layoutStore.save(selectedTopologyOwnerId.value, positions);
    },
  }));
  // Browser tests reach the canvas through this (never in a production build)
  if (import.meta.env.DEV) window.__blabCanvas = canvas;
  document.addEventListener('keydown', onKey);

  await Promise.all([fetchSharedTopologies(), fetchUsers(), fetchLabStatus()]);
  await fetchData(selectedTopologyOwnerId.value);
});

usePoll(() => updateTopology(), 2000, { immediate: false });
// Quarantines and holders change rarely: the Lab status is read less often
usePoll(() => fetchLabStatus(), 30000, { immediate: false });

onUnmounted(() => {
  layoutStore.flush();  // a drag just before leaving the page is saved all the same
  document.removeEventListener('keydown', onKey);
  if (import.meta.env.DEV) delete window.__blabCanvas;
  canvas?.destroy();
  canvas = null;
});

// --- Topology View Logic ---
const fetchSharedTopologies = async () => {
  const result = await topologyService.getShared();
  topologiesSharedWithMe.value = result.success ? result.data?.shared_with_me || [] : [];
  topologiesIShared.value = result.success ? result.data?.shared_by_me || [] : [];
};

const fetchUsers = async () => {
  const result = await userService.getAll();
  users.value = result.success ? result.data?.users || [] : [];
};

// Who holds each Switch and which are in Quarantine: the Lab status knows, the Topology doesn't
const fetchLabStatus = async () => {
  const result = await labStatusService.get();
  if (result.success) {
    const switches = result.data?.switches || [];
    labStatus.value = {
      quarantines: new Map(switches.filter(s => s.quarantine).map(s => [s.id, s.quarantine])),
      holders: Object.fromEntries(switches.filter(s => s.holder).map(s => [s.id, s.holder])),
    };
    redraw();
  }
  return result;
};

const onTopologyViewChange = async () => {
  topology = null;
  fitPending = true;
  cancelConnect();
  select(null);
  canvas.clear();
  isLoading.value = true;
  savedPositions = {};
  await fetchData(selectedTopologyOwnerId.value);
};

// --- Share Topology ---
const shareTopology = async () => {
  const userObj = availableUsers.value.find(u => u.id === shareTargetUserId.value);
  if (!userObj) return;
  sharing.value = true;
  const result = await topologyService.share(userObj.username);
  sharing.value = false;
  if (result.success) {
    toast.success(`Topology shared with ${userObj.username}.`);
    shareTargetUserId.value = '';
    fetchSharedTopologies();
  } else {
    toast.error(`Couldn't share your Topology. ${result.message}`);
  }
};

const unshareTopology = async (shareId) => {
  const result = await topologyService.unshare(shareId);
  if (result.success) {
    toast.success('Sharing stopped.');
    fetchSharedTopologies();
  } else {
    toast.error(`Couldn't stop sharing. ${result.message}`);
  }
};

// --- Drawing ---
// The server owns the Topology: its Switches, their Ports and every Link. This page only draws it.
// Returns the API result, for the poll to tell whether BLab answered
const fetchData = async (ownerId) => {
  const result = await topologyService.get(ownerId);
  // A late reply for a Topology we have since switched away from
  if (String(ownerId) !== String(selectedTopologyOwnerId.value)) return null;
  if (!result.success) {
    if (String(ownerId) !== String(myUserId.value) && [403, 404].includes(result.status)) {
      // The Topology is no longer shared with us: back to our own
      selectedTopologyOwnerId.value = myUserId.value;
      toast.error('This Topology is no longer shared with you.');
      await fetchSharedTopologies();
      await onTopologyViewChange();
    } else if (isLoading.value) {
      // Unreachable: the poll says so and tries again; anything else is said here
      if (result.status && result.status < 500) toast.error(`Couldn't load the Topology. ${result.message}`, 'topology-load');
      isLoading.value = false;
    }
    return result;
  }
  // The layout comes with the Topology: the same for everyone viewing it
  const positions = await layoutStore.load(ownerId, result.data);
  if (String(ownerId) !== String(selectedTopologyOwnerId.value)) return null;
  savedPositions = positions;
  mayWork.value = result.data.may_work;
  topology = result.data;
  isLoading.value = false;
  redraw();
  return result;
};

const updateTopology = () => fetchData(selectedTopologyOwnerId.value);

const ownerName = () => users.value.find(u => String(u.id) === String(selectedTopologyOwnerId.value))?.username || null;

function redraw() {
  if (!topology || !canvas) return;
  const holders = { ...labStatus.value.holders };
  // The Topology's own Switches are held by its owner, whatever the Lab status last said
  const owner = ownerName();
  if (owner) topology.switches.filter(s => s.in_topology).forEach(s => { holders[s.id] = owner; });
  const elements = buildElements(topology, {
    quarantines: new Set(labStatus.value.quarantines.keys()),
    holders,
    connecting: connecting.value,
    disconnecting,
    inFlight,
  });
  canvas.sync(elements, savedPositions);
  isEmpty.value = elements.switches.length === 0;
  if (fitPending && !isEmpty.value) {
    canvas.fit();
    fitPending = false;
  }
  if (selectedId.value && !canvas.has(selectedId.value)) select(null);
  if (connectFrom.value) {
    const source = canvas.data(connectFrom.value.id);
    if (!source || whyNoConnect(source, { mayWork: mayWork.value })) cancelConnect();
    else canvas.setConnect({ source: source.id, targets: connectTargets(source) });
  }
  redraws.value++;
}

// --- Selection ---
function select(id) {
  selectedId.value = id;
  canvas?.select(id);
}

const selected = computed(() => {
  redraws.value;  // follow every redraw
  return canvas?.data(selectedId.value) || null;
});

// The Link drawn on a port, and the port at its other end
const selectedPortLink = computed(() => (selected.value?.type === 'port' ? canvas.linkOf(selected.value.id) : null));
const selectedPortOtherEnd = computed(() => {
  const link = selectedPortLink.value;
  if (!link) return null;
  return canvas.data(link.source === selected.value.id ? link.target : link.source);
});
// The Disconnect button of the selected Link (or of the selected port's Link) is pending
const selectedDisconnectRunning = computed(() => {
  const link = selected.value?.type === 'link' ? selected.value : selectedPortLink.value;
  return !!link && disconnectRequests.value.has(link.svlan);
});
const selectedLinkEnds = computed(() => (selected.value?.type === 'link' ? canvas.ends(selected.value.id) : []));
const selectedQuarantine = computed(() => (selected.value?.type === 'switch'
  ? labStatus.value.quarantines.get(selected.value.switchId) || null : null));

function onTap(data) {
  hover.value = null;
  if (connectFrom.value) {
    if (data?.type === 'port') pickConnectTarget(data);
    return;
  }
  select(data ? data.id : null);
}

function onKey(event) {
  if (event.key !== 'Escape') return;
  // A dialog closes itself first
  if (confirm.value || switchToReleaseId.value !== null || showSharePopup.value) return;
  if (connectFrom.value) cancelConnect();
  else select(null);
}

const linkTitle = (link) => {
  const [a, b] = canvas.ends(link.id);
  if (!a || !b) return '';
  const svlan = link.svlan ? `, SVLAN ${link.svlan}` : '';
  const state = link.state === 'up' ? '' : ` (${{ connecting: 'being connected', disconnecting: 'being disconnected', failed: 'disconnect failed' }[link.state]})`;
  return `${a.fullName} to ${b.fullName}${svlan}${state}`;
};

// --- Controls ---
async function rearrange() {
  canvas.rearrange();
  savedPositions = {};
  if (mayWork.value) await layoutStore.forget(selectedTopologyOwnerId.value);
}

const controls = [
  { label: 'Zoom in', icon: MagnifyingGlassPlusIcon, action: () => canvas.zoomBy(1.25) },
  { label: 'Zoom out', icon: MagnifyingGlassMinusIcon, action: () => canvas.zoomBy(0.8) },
  { label: 'Fit to screen', icon: ArrowsPointingInIcon, action: () => canvas.fit() },
  { label: 'Re-arrange', icon: ArrowPathIcon, action: rearrange },
];

// --- Connect ---
const connectTargets = (source) => canvas.ports()
  .filter(port => port.id !== source.id && !whyNoConnect(port, { mayWork: mayWork.value }))
  .map(port => port.id);

function startConnect(port) {
  connectFrom.value = port;
  connectHint.value = '';
  select(port.id);
  canvas.setConnect({ source: port.id, targets: connectTargets(port) });
}

function cancelConnect() {
  connectFrom.value = null;
  connectHint.value = '';
  canvas?.setConnect(null);
}

function pickConnectTarget(port) {
  const from = connectFrom.value;
  if (port.id === from.id) {
    connectHint.value = 'Click another port.';
    return;
  }
  const why = whyNoConnect(port, { mayWork: mayWork.value });
  if (why) {
    connectHint.value = `${port.fullName}: ${why}`;
    return;
  }
  confirm.value = {
    title: 'Connect these ports?',
    message: `BLab makes a Link between ${from.fullName} and ${port.fullName}.`,
    label: 'Connect',
    danger: false,
    action: () => connect(from, port),
  };
}

async function connect(a, b) {
  cancelConnect();
  connecting.value = { portA: a.portId, portB: b.portId };
  redraw();
  const result = await portService.connect(a.portId, b.portId);
  connecting.value = null;
  if (result.success) {
    toast.success(`Link made between ${a.fullName} and ${b.fullName}.`);
    await updateTopology();
    // Show the new Link
    const svlan = canvas.data(a.id)?.svlan;
    if (svlan != null && canvas.has(linkEdgeId(svlan))) select(linkEdgeId(svlan));
  } else {
    redraw();
    toast.error(`Couldn't connect the ports. ${result.message}`);
  }
}

// --- Disconnect ---
function askDisconnect(link) {
  if (whyNoDisconnect(link, { mayWork: mayWork.value })) return;
  const [a, b] = canvas.ends(link.id);
  if (!a || !b) return;  // gone meanwhile
  confirm.value = {
    title: 'Disconnect this Link?',
    message: link.teardownError
      ? `Disconnecting the Link between ${a.fullName} and ${b.fullName} failed: ${plainMessage(link.teardownError)} Try again?`
      : `BLab removes the Link between ${a.fullName} and ${b.fullName} (SVLAN ${link.svlan}) from the backbones.`,
    label: 'Disconnect',
    danger: true,
    action: () => disconnect(link),
  };
}

async function disconnect(link) {
  const [a, b] = canvas.ends(link.id);
  if (!a || !b) {  // gone while the confirmation was open
    toast.error("Couldn't disconnect the Link: it is no longer in this Topology.");
    return;
  }
  disconnecting.set(link.svlan, canvas.snapshot(link.id));
  inFlight.add(link.svlan);
  disconnectRequests.value = new Set([...disconnectRequests.value, link.svlan]);
  redraw();
  const result = await portService.disconnect(a.portId, b.portId);
  inFlight.delete(link.svlan);
  disconnectRequests.value = new Set([...disconnectRequests.value].filter(v => v !== link.svlan));
  if (result.success) {
    toast.success('Disconnecting the Link.');
  } else {
    disconnecting.delete(link.svlan);
    toast.error(`Couldn't disconnect the Link. ${result.message}`);
  }
  await updateTopology();
}

// --- Confirmation ---
function runConfirm() {
  const action = confirm.value?.action;
  confirm.value = null;
  if (action) action();
}

// Cancelling the Connect confirmation goes back to choosing the other port
function closeConfirm() {
  confirm.value = null;
}
</script>
