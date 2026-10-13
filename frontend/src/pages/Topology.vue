<template>
  <div class="flex min-h-0 flex-1 flex-col bg-white">
    <div class="flex flex-wrap items-center gap-3 border-b border-gray-200 px-4 py-3">
      <UiButton to="/topology" size="sm" :aria-label="projectsOn ? 'Back to my Projects' : 'Back to the Topologies'">
        <ArrowLeftIcon class="h-4 w-4" aria-hidden="true" />{{ projectsOn ? 'Projects' : 'Topologies' }}
      </UiButton>
      <div class="min-w-0">
        <h1 class="truncate text-lg font-semibold text-gray-900">{{ title }}</h1>
        <p v-if="subtitle" class="truncate text-xs text-gray-500">{{ subtitle }}</p>
      </div>
      <div class="ml-auto flex flex-wrap items-center gap-2">
        <UiButton v-if="mayWork" variant="primary" :disabled="!canLink" @click="openNewLink(null)">New Link</UiButton>
        <UiButton :to="{ path: '/', hash: '#switch-accounts' }">My Switch accounts</UiButton>
        <UiButton v-if="projectsOn" to="/projects">Members and Invite link</UiButton>
        <UiButton v-else @click="showSharePopup = true">Share Topology</UiButton>
      </div>
    </div>

    <div class="flex min-h-0 flex-1">
      <div ref="canvasArea" class="relative min-h-0 flex-1 overflow-hidden">
        <div ref="cyContainer" class="absolute inset-0" data-testid="topology-canvas"></div>

        <!-- Drag from the handle on a card to another Switch to make a Link -->
        <button
          v-if="handle"
          type="button"
          class="absolute z-10 flex h-6 w-6 -translate-x-1/2 -translate-y-1/2 cursor-crosshair items-center justify-center rounded-full border-2 border-white bg-primary-600 text-white shadow ring-1 ring-primary-700 hover:bg-primary-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-600"
          :style="{ left: `${handle.x}px`, top: `${handle.y}px` }"
          :aria-label="`New Link from ${handle.name}: drag to another Switch`"
          :title="'Drag to another Switch to make a Link'"
          data-testid="connect-handle"
          @pointerdown="startDrag"
          @pointerenter="handleHovered = true"
          @pointerleave="handleHovered = false; onHoverSwitch(null)"
          @click="openNewLink(handle.switchId)"
        >
          <PlusIcon class="h-4 w-4" aria-hidden="true" />
        </button>
        <svg v-if="drag" class="pointer-events-none absolute inset-0 z-10 h-full w-full" aria-hidden="true">
          <line :x1="drag.x0" :y1="drag.y0" :x2="drag.x" :y2="drag.y" :stroke="COLORS.primary[600]" stroke-width="3" stroke-dasharray="8 5" stroke-linecap="round" />
        </svg>

        <!-- The New Link popover, where a drag was dropped -->
        <div
          v-if="popover"
          role="dialog"
          aria-label="New Link"
          class="absolute z-20 w-72 rounded-lg border border-gray-200 bg-white p-3 shadow-lg"
          :style="{ left: `${popover.left}px`, top: `${popover.top}px` }"
        >
          <h2 class="mb-2 text-sm font-semibold text-gray-900">New Link</h2>
          <NewLinkForm
            fixed
            id-prefix="popover-link"
            :switches="drawnSwitches"
            :ports="ports"
            :may-work="mayWork"
            :from="popover.from"
            :to="popover.to"
            :pending="!!connecting"
            @connect="connect"
            @cancel="popover = null"
          />
        </div>

        <!-- Controls -->
        <div class="absolute right-3 top-3 z-10 flex flex-col gap-1 rounded-lg border border-gray-200 bg-white/95 p-1 shadow-sm">
          <button v-for="control in controls" :key="control.label" type="button" :title="control.label" :aria-label="control.label"
            class="rounded p-1.5 text-gray-700 hover:bg-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-600"
            @click="control.action">
            <component :is="control.icon" class="h-5 w-5" aria-hidden="true" />
          </button>
        </div>

        <TopologyLegend
          v-if="!isEmpty && !isLoading"
          class="absolute bottom-3 left-3 z-10"
          :switch-states="present.switches"
          :link-states="present.links"
        />

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
            <p v-if="projectsOn || isMine" class="mt-1">
              Reserve Switches on the <router-link to="/reservation" class="font-medium text-primary-700 underline">Reservation</router-link> page to see them here.
            </p>
          </div>
        </div>
      </div>

      <TopologySidePanel
        v-if="selected || newLink"
        :item="selected"
        :new-link="newLink"
        :switches="drawnSwitches"
        :links="drawnLinks"
        :ports="ports"
        :quarantine="selectedQuarantine"
        :may-work="mayWork"
        :busy="selectedDisconnectRunning"
        :connecting="!!connecting"
        @close="closePanel"
        @connect="connect"
        @new-link="openNewLink"
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
      @close="confirm = null"
      @confirm="runConfirm"
    />

    <!-- Sharing the Topology (Projects off) -->
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
// The Topology canvas (see CONTEXT.md) of one Project (/topology/<project id>) or, with
// Projects off, of one user (/topology/user/<owner id>); the picker (TopologyPicker.vue) leads
// here. Each Switch is a card; click one, or a Link, to see it in the side panel. A new Link is
// made by dragging from a card's handle to another Switch, then picking a port on each side in
// a popover, or through the New Link form in the side panel: both connect the same way.
// What is drawn comes from topology/model.js; topology/canvas.js keeps cytoscape in step with
// it on every 2 s refresh without moving anything.
import { computed, markRaw, onMounted, onUnmounted, ref, shallowRef, watch } from 'vue';
import {
  ArrowLeftIcon, ArrowPathIcon, ArrowsPointingInIcon, MagnifyingGlassMinusIcon, MagnifyingGlassPlusIcon, PlusIcon,
} from '@heroicons/vue/24/outline';
import { useRoute, useRouter } from 'vue-router';
import ReleaseDialog from '../components/ReleaseDialog.vue';
import NewLinkForm from '../components/topology/NewLinkForm.vue';
import TopologyLegend from '../components/topology/TopologyLegend.vue';
import TopologySidePanel from '../components/topology/TopologySidePanel.vue';
import UiButton from '../components/ui/UiButton.vue';
import UiConfirm from '../components/ui/UiConfirm.vue';
import UiModal from '../components/ui/UiModal.vue';
import UiSpinner from '../components/ui/UiSpinner.vue';
import { createCanvas } from '../topology/canvas.js';
import { layoutStore } from '../topology/layoutStore.js';
import {
  COLORS, LINK_STATES, SWITCH_STATES, buildElements, linkEdgeId, switchNodeId, whyNoDisconnect, whyNoLinkOn,
} from '../topology/model.js';
import { labStatusService, portService, projectService, userService, topologyService } from '../utils/apiService.js';
import { loadProjects } from '../utils/projects.js';
import { plainMessage } from '../utils/errorHandler.js';
import { getCurrentUserId, session } from '../auth.js';
import { toast } from '../composables/toast.js';
import { usePoll } from '../composables/poll.js';
import { plural } from '../utils/plural.js';

const route = useRoute();
const router = useRouter();

// --- State ---
const cyContainer = ref(null);
const canvasArea = ref(null);
const isLoading = ref(true);
const isEmpty = ref(false);
const mayWork = ref(false); // whether we may connect, disconnect and release in the Topology shown
const confirm = ref(null);  // { title, message, label, danger, action } while asking
const switchToReleaseId = ref(null);
let canvas = null;

// What the canvas draws besides the API's answer
let topology = null;              // the last answer for the Topology shown
let savedPositions = {};          // Switch positions of the Topology shown (layoutStore)
let fitPending = true;            // fit the view once the Topology shown is first drawn
const labStatus = shallowRef({ quarantines: new Map(), holders: {} });
const connecting = ref(null);     // { portA, portB } while a connect request runs
const disconnecting = new Map();  // svlan -> what was drawn, while the Link worker tears it down
const inFlight = new Set();       // SVLANs whose disconnect request is running
const disconnectRequests = ref(new Set());  // the same, for the Disconnect buttons to show it

// What was last drawn, for the side panel, the New Link form and the legend
const ports = shallowRef([]);
const drawnSwitches = shallowRef([]);
const drawnLinks = shallowRef([]);

// Selection, the New Link form, hover. The panel follows `redraws`, counted on every redraw.
const redraws = ref(0);
const selectedId = ref(null);
const newLink = ref(null);        // { from, to }: Switch ids, while the side panel's form is open
const hover = ref(null);          // { text, x, y }

// Sharing state (Projects off)
const showSharePopup = ref(false);
const topologiesSharedWithMe = ref([]);
const topologiesIShared = ref([]);
const users = ref([]);
const shareTargetUserId = ref('');
const sharing = ref(false);
const myUserId = ref('');

// The Topology shown: a Project's with Projects on, else a user's
const projectsOn = ref(!!session.projects);
const project = ref(null);  // its summary, as projects/<id>/topology/ gives it
const viewId = () => String((projectsOn.value ? route.params.project : route.params.owner) || '');
// Where the layout of the Topology shown is kept (layoutStore)
const layoutKey = () => (projectsOn.value ? `p${viewId()}` : viewId());
const isMine = computed(() => !projectsOn.value && String(route.params.owner) === String(myUserId.value));

const availableUsers = computed(() => users.value.filter(u => String(u.id) !== String(myUserId.value)));

const ownerName = () => users.value.find(u => String(u.id) === String(route.params.owner))?.username
  || topologiesSharedWithMe.value.find(s => String(s.owner_id) === String(route.params.owner))?.owner_username || null;

const title = computed(() => {
  if (projectsOn.value) return project.value?.name || 'Project';
  return isMine.value ? 'My Topology' : `${ownerName() || 'Someone'}'s Topology`;
});
const subtitle = computed(() => {
  if (!projectsOn.value || !project.value) return '';
  return [project.value.reference, plural(project.value.members?.length || 0, 'member', 'members')].filter(Boolean).join(' · ');
});

// --- Init ---
onMounted(async () => {
  myUserId.value = String(getCurrentUserId() || '');
  canvas = markRaw(createCanvas(cyContainer.value, {
    onTap,
    onHover: (h) => { hover.value = h && { text: linkTitle(h.data), x: h.x, y: h.y }; },
    onHoverSwitch,
    onViewChange: () => { viewTicks.value++; },
    onSwitchesMoved: (positions) => {
      savedPositions = positions;
      if (mayWork.value) layoutStore.save(layoutKey(), positions);
    },
  }));
  // Browser tests reach the canvas through this (never in a production build)
  if (import.meta.env.DEV) window.__blabCanvas = canvas;
  document.addEventListener('keydown', onKey);

  const projects = await loadProjects();
  projectsOn.value = projects.enabled ?? !!session.projects;  // BLab couldn't say: what it said last
  // An address of the other kind (bookmarked before Projects were turned on, or off)
  if (projectsOn.value !== !!route.params.project) {
    toast.error(projectsOn.value ? 'Topologies belong to Projects now: choose one.' : 'BLab has no Projects yet: choose a Topology.');
    router.replace('/topology');
    return;
  }
  await Promise.all([projectsOn.value ? null : fetchSharedTopologies(), projectsOn.value ? null : fetchUsers(), fetchLabStatus()]);
  await fetchData(viewId());
});

// Another Topology at the same page (a link, or back to my own)
watch(() => route.fullPath, (to, from) => {
  if (to !== from && (route.params.project || route.params.owner)) showOther();
});

usePoll(() => updateTopology(), 2000, { immediate: false });
// Quarantines and holders change rarely: the Lab status is read less often
usePoll(() => fetchLabStatus(), 30000, { immediate: false });

onUnmounted(() => {
  layoutStore.flush();  // a drag just before leaving the page is saved all the same
  document.removeEventListener('keydown', onKey);
  stopDragListeners();
  clearTimeout(hoverTimer);
  if (import.meta.env.DEV) delete window.__blabCanvas;
  canvas?.destroy();
  canvas = null;
});

// --- Who holds what, and sharing (Projects off) ---
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

async function showOther() {
  topology = null;
  project.value = null;
  fitPending = true;
  closePanel();
  popover.value = null;
  cancelDrag();
  canvas?.clear();
  isLoading.value = true;
  isEmpty.value = false;
  savedPositions = {};
  await fetchData(viewId());
}

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
const fetchData = async (id) => {
  if (!id || !canvas) return null;
  const result = await (projectsOn.value ? projectService.topology(id) : topologyService.get(id));
  // A late reply for a Topology we have since left
  if (id !== viewId()) return null;
  if (!result.success) {
    if (projectsOn.value && result.status === 404) {
      // Not a member (left, removed, or never was), or Projects were turned off: back to the picker
      const projects = await loadProjects();
      toast.error(projects.enabled === false ? 'BLab has no Projects any more: choose a Topology.' : 'You are not a member of this Project.');
      router.replace('/topology');
    } else if (!projectsOn.value && id !== myUserId.value && [403, 404].includes(result.status)) {
      // The Topology is no longer shared with us: back to our own
      toast.error('This Topology is no longer shared with you.');
      await fetchSharedTopologies();
      router.replace(`/topology/user/${myUserId.value}`);
    } else if (isLoading.value) {
      // Unreachable: the poll says so and tries again; anything else is said here
      if (result.status && result.status < 500) toast.error(`Couldn't load the Topology. ${result.message}`, 'topology-load');
      isLoading.value = false;
    }
    return result;
  }
  // The layout comes with the Topology: the same for everyone viewing it
  const positions = await layoutStore.load(layoutKey(), result.data);
  if (id !== viewId()) return null;
  savedPositions = positions;
  mayWork.value = result.data.may_work;
  if (result.data.project) project.value = result.data.project;
  topology = result.data;
  isLoading.value = false;
  redraw();
  return result;
};

const updateTopology = () => fetchData(viewId());

function redraw() {
  if (!topology || !canvas) return;
  const holders = { ...labStatus.value.holders };
  // The Topology's own Switches are held by its owner, whatever the Lab status last said
  const owner = projectsOn.value ? null : ownerName();
  if (owner) topology.switches.filter(s => s.in_topology).forEach(s => { holders[s.id] = owner; });
  const elements = buildElements(topology, {
    quarantines: new Set(labStatus.value.quarantines.keys()),
    holders,
    connecting: connecting.value,
    disconnecting,
    inFlight,
  });
  canvas.sync(elements, savedPositions);
  ports.value = elements.ports;
  drawnSwitches.value = canvas.switches();
  drawnLinks.value = canvas.links();
  isEmpty.value = elements.switches.length === 0;
  if (fitPending && !isEmpty.value) {
    canvas.fit();
    fitPending = false;
  }
  if (selectedId.value && !canvas.has(selectedId.value)) select(null);
  // A Switch of the open popover is gone (Released)
  if (popover.value && !(canvas.has(switchNodeId(popover.value.from)) && canvas.has(switchNodeId(popover.value.to)))) popover.value = null;
  redraws.value++;
}

// The states drawn right now, for the legend
const present = computed(() => {
  const switches = new Set(drawnSwitches.value.map(s => s.state));
  const links = new Set(drawnLinks.value.map(l => l.state));
  return {
    switches: Object.keys(SWITCH_STATES).filter(s => switches.has(s)),
    links: Object.keys(LINK_STATES).filter(s => links.has(s)),
  };
});

// --- Selection ---
function select(id) {
  selectedId.value = id;
  if (id) newLink.value = null;
  canvas?.select(id);
}

function closePanel() {
  newLink.value = null;
  select(null);
}

const selected = computed(() => {
  redraws.value;  // follow every redraw
  // A copy: cytoscape changes its data in place, and the side panel must see the new state
  const data = canvas?.data(selectedId.value);
  return data ? { ...data } : null;
});

// The Disconnect button of the selected Link is pending
const selectedDisconnectRunning = computed(() => selected.value?.type === 'link' && disconnectRequests.value.has(selected.value.svlan));
const selectedQuarantine = computed(() => (selected.value?.type === 'switch'
  ? labStatus.value.quarantines.get(selected.value.switchId) || null : null));

function onTap(data) {
  hover.value = null;
  popover.value = null;
  if (data) select(data.id);
  else closePanel();
}

function onKey(event) {
  if (event.key !== 'Escape') return;
  // A dialog closes itself first
  if (confirm.value || switchToReleaseId.value !== null || showSharePopup.value) return;
  if (drag.value) cancelDrag();
  else if (popover.value) popover.value = null;
  else closePanel();
}

const linkTitle = (link) => {
  const [a, b] = link.ends || [];
  if (!a || !b) return '';
  const svlan = link.svlan ? `, SVLAN ${link.svlan}` : '';
  const state = ['up', 'ghost'].includes(link.state) ? '' : ` (${LINK_STATES[link.state].label.toLowerCase()})`;
  return `${a.fullName} to ${b.fullName}${svlan}${state}`;
};

// --- Controls ---
async function rearrange() {
  canvas.rearrange();
  savedPositions = {};
  if (mayWork.value) await layoutStore.forget(layoutKey());
}

const controls = [
  { label: 'Zoom in', icon: MagnifyingGlassPlusIcon, action: () => canvas.zoomBy(1.25) },
  { label: 'Zoom out', icon: MagnifyingGlassMinusIcon, action: () => canvas.zoomBy(0.8) },
  { label: 'Fit to screen', icon: ArrowsPointingInIcon, action: () => canvas.fit() },
  { label: 'Re-arrange', icon: ArrowPathIcon, action: rearrange },
];

// --- New Link: drag from a card's handle, or the side panel's form ---
const may = () => ({ mayWork: mayWork.value });
// The Switches a Link may start or end on
const linkable = (sw) => !whyNoLinkOn(sw, ports.value, may());
const canLink = computed(() => drawnSwitches.value.some(linkable));

function openNewLink(fromSwitchId) {
  popover.value = null;
  const from = fromSwitchId ?? drawnSwitches.value.find(linkable)?.switchId ?? null;
  select(null);
  newLink.value = { from, to: null };
}

// The handle sits on the right edge of the card under the pointer, or of the selected one
const hoveredSwitchId = ref(null);
const handleHovered = ref(false);
const viewTicks = ref(0);
let hoverTimer = null;

function onHoverSwitch(id) {
  clearTimeout(hoverTimer);
  if (id) {
    hoveredSwitchId.value = id;
  } else {
    // Long enough to reach the handle, which sits half off the card
    hoverTimer = setTimeout(() => { if (!handleHovered.value) hoveredSwitchId.value = null; }, 400);
  }
}

const handle = computed(() => {
  viewTicks.value;  // follow pan, zoom and drags
  redraws.value;
  if (!canvas || !mayWork.value || drag.value || popover.value) return null;
  const id = hoveredSwitchId.value || (selected.value?.type === 'switch' ? selected.value.id : null);
  const data = canvas.data(id);
  if (!data || !linkable(data)) return null;
  const box = canvas.renderedBox(id);
  return { id, switchId: data.switchId, name: data.name, x: box.x2, y: (box.y1 + box.y2) / 2 };
});

const drag = ref(null);     // { from, x0, y0, x, y } while a Link is dragged out of a card
const popover = ref(null);  // { from, to, left, top }: Switch ids, where the drag was dropped

const pointIn = (event) => {
  const box = cyContainer.value.getBoundingClientRect();
  return { x: event.clientX - box.left, y: event.clientY - box.top };
};
const dropTarget = (point) => {
  const over = canvas.switchAt(point.x, point.y);
  return over && over.id !== drag.value.from.id ? over : null;
};
const connectTargets = (from) => drawnSwitches.value.filter(sw => sw.id !== from.id && linkable(sw)).map(sw => sw.id);

function startDrag(event) {
  const h = handle.value;
  if (!h || event.button !== 0) return;
  event.preventDefault();
  const from = canvas.data(h.id);
  drag.value = { from, moved: false, x0: h.x, y0: h.y, x: h.x, y: h.y };
  canvas.setConnect({ source: from.id, targets: connectTargets(from), drop: null });
  window.addEventListener('pointermove', moveDrag);
  window.addEventListener('pointerup', endDrag);
  window.addEventListener('pointercancel', cancelDrag);
}

// Farther than a shaky click: a drag
const DRAG_THRESHOLD = 6;

function moveDrag(event) {
  if (!drag.value) return;
  const point = pointIn(event);
  const moved = drag.value.moved || Math.hypot(point.x - drag.value.x0, point.y - drag.value.y0) > DRAG_THRESHOLD;
  drag.value = { ...drag.value, ...point, moved };
  const target = dropTarget(point);
  canvas.setConnect({ source: drag.value.from.id, targets: connectTargets(drag.value.from), drop: target?.id || null });
}

function endDrag(event) {
  if (!drag.value) return;
  const point = pointIn(event);
  const { from, moved } = drag.value;
  const target = dropTarget(point);
  cancelDrag();
  if (!moved) {
    // A click on the handle: the side panel's New Link form
    openNewLink(from.switchId);
    return;
  }
  if (!target) return;
  // The popover beside the drop, inside the canvas
  const area = canvasArea.value.getBoundingClientRect();
  popover.value = {
    from: from.switchId,
    to: target.switchId,
    left: Math.max(8, Math.min(point.x + 12, area.width - 296)),
    top: Math.max(8, Math.min(point.y - 20, area.height - 300)),
  };
  closePanel();
}

function stopDragListeners() {
  window.removeEventListener('pointermove', moveDrag);
  window.removeEventListener('pointerup', endDrag);
  window.removeEventListener('pointercancel', cancelDrag);
}

function cancelDrag() {
  stopDragListeners();
  drag.value = null;
  canvas?.setConnect(null);
}

// Both ways of making a Link end here: one port on each side
async function connect({ a, b }) {
  connecting.value = { portA: a.portId, portB: b.portId };
  redraw();
  const result = await portService.connect(a.portId, b.portId);
  connecting.value = null;
  if (result.success) {
    popover.value = null;
    newLink.value = null;
    toast.success(`Link made between ${a.fullName} and ${b.fullName}.`);
    await updateTopology();
    // Show the new Link
    const svlan = ports.value.find(p => p.portId === a.portId)?.svlan;
    if (svlan != null && canvas?.has(linkEdgeId(svlan))) select(linkEdgeId(svlan));
  } else {
    // The form stays open, to try again or pick other ports
    redraw();
    toast.error(`Couldn't connect the ports. ${result.message}`);
  }
}

// --- Disconnect ---
function askDisconnect(link) {
  if (whyNoDisconnect(link, may())) return;
  const [a, b] = link.ends;
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
  if (!canvas.has(link.id)) {  // gone while the confirmation was open
    toast.error("Couldn't disconnect the Link: it is no longer in this Topology.");
    return;
  }
  const [a, b] = link.ends;
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
</script>
