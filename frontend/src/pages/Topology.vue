<template>
  <div @contextmenu.prevent class="flex min-h-0 flex-1 flex-col bg-white">
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
    <div class="relative min-h-0 flex-1">
      <div ref="cyContainer" class="absolute inset-0"></div>
      <div v-if="isLoading" class="absolute inset-0 flex items-center justify-center bg-white/50 text-primary-700">
        <UiSpinner size="lg" />
      </div>
    </div>
    <HelpBall @toggle="toggleHelp" />
    <HelpPanel v-if="showHelp" />
    <UiConfirm
      v-if="confirm"
      :title="confirm.title"
      :message="confirm.message"
      :confirm-label="confirm.label"
      :danger="confirm.danger"
      @close="handleConfirmClose"
      @confirm="handleConfirm"
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
import { ref, onMounted, onUnmounted } from 'vue';
import { useRoute } from 'vue-router';
import cytoscape from 'cytoscape';
import HelpBall from '../components/HelpBall.vue';
import HelpPanel from '../components/HelpPanel.vue';
import ReleaseDialog from '../components/ReleaseDialog.vue';
import UiButton from '../components/ui/UiButton.vue';
import UiConfirm from '../components/ui/UiConfirm.vue';
import UiModal from '../components/ui/UiModal.vue';
import UiSpinner from '../components/ui/UiSpinner.vue';
import { debounce } from 'lodash';
import { portService, userService, topologyService } from '../utils/apiService.js';
import { plainMessage } from '../utils/errorHandler.js';
import { getCurrentUserId } from '../auth.js';
import { toast } from '../composables/toast.js';
import { usePoll } from '../composables/poll.js';

// --- State ---
const cyContainer = ref(null);
const showHelp = ref(false);
const isLoading = ref(false);
const mayWork = ref(false); // whether we may connect, disconnect and release in the topology shown
const layoutPositions = ref({}); // Will now store per-topology layouts
const confirm = ref(null);  // { title, message, label, danger, action } while asking
const isDragging = ref(false);
let cy;
const route = useRoute();

// Sharing state
const showSharePopup = ref(false);
const topologiesSharedWithMe = ref([]);
const topologiesIShared = ref([]);
const availableUsers = ref([]);
const shareTargetUserId = ref('');
const sharing = ref(false);
const selectedTopologyOwnerId = ref('');
const myUserId = ref('');
const selectedPorts = ref([]); // Add selectedPorts back for port connection functionality
const switchToReleaseId = ref(null);

// Helper function for context menu prevention
function preventContext(event) {
  event.preventDefault();
}

const toggleHelp = () => {
  showHelp.value = !showHelp.value;
};

// --- Init ---
onMounted(async () => {
  // user is just a string id in localStorage
  myUserId.value = getCurrentUserId() || '';
  // ?owner=<id> opens a Topology shared with me (links from My lab)
  selectedTopologyOwnerId.value = route.query.owner || myUserId.value;

  await fetchSharedTopologies();
  await fetchAvailableUsers();
  setupCytoscape();
  setTimeout(async () => {
    await fetchData(selectedTopologyOwnerId.value);
  }, 0);
  document.addEventListener('contextmenu', preventContext);
});

usePoll(() => updateTopology(), 2000, { immediate: false });

onUnmounted(() => {
  saveLayoutPositions();
  document.removeEventListener('contextmenu', preventContext);
});

// --- Topology View Logic ---
const fetchSharedTopologies = async () => {
  try {
    const result = await topologyService.getShared();
    if (result.success) {
      const data = result.data || {};

      // Handle the new API response format
      topologiesSharedWithMe.value = data.shared_with_me || [];
      topologiesIShared.value = data.shared_by_me || [];
    } else {
      console.error('Error fetching shared topologies:', result.message);
      topologiesSharedWithMe.value = [];
      topologiesIShared.value = [];
    }
  } catch (e) {
    console.error('Error fetching shared topologies:', e);
    topologiesSharedWithMe.value = [];
    topologiesIShared.value = [];
  }
};

const fetchAvailableUsers = async () => {
  try {
    const result = await userService.getAll();
    if (result.success) {
      // user is just a string id
      const userId = getCurrentUserId();
      availableUsers.value = (result.data?.users || []).filter(u => String(u.id) !== String(userId));
    } else {
      console.error('Error fetching available users:', result.message);
      availableUsers.value = [];
    }
  } catch (e) {
    console.error('Error fetching available users:', e);
    availableUsers.value = [];
  }
};

// Helper to get a unique key for the current topology view
function getLayoutKey() {
  // Use the selectedTopologyOwnerId as the key for the layout
  return `topologyLayout_${selectedTopologyOwnerId.value}`;
}

const onTopologyViewChange = async () => {
  loadLayoutFromStorage();
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

// --- Cytoscape Logic ---
// The server owns the Topology: its Switches, their Ports and every Link. This page only draws it.
// Returns the API result, for the poll to tell whether BLab answered
const fetchData = async (ownerId) => {
  const result = await topologyService.get(ownerId);
  // A late reply for a topology we have since switched away from
  if (String(ownerId) !== String(selectedTopologyOwnerId.value)) return null;
  if (!result.success) {
    console.error('Failed to fetch topology:', result.message);
    if (String(ownerId) !== String(myUserId.value) && [403, 404].includes(result.status)) {
      // The topology is no longer shared with us: back to our own
      selectedTopologyOwnerId.value = myUserId.value;
      toast.error('This Topology is no longer shared with you.');
      await fetchSharedTopologies();
      await onTopologyViewChange();
    }
    return result;
  }
  mayWork.value = result.data.may_work;
  if (cy) {
    cy.json({ elements: createElements(result.data) });
    // Don't run layout automatically to preserve zoom
    cy.nodes().forEach(node => {
      const pos = layoutPositions.value[node.id()];
      if (pos) node.position(pos);
    });
  }
  return result;
};

const createElements = ({ switches, ports, links }) => {
  const elements = [];
  for (const sw of switches) {
    const switchPorts = ports.filter(port => port.switch === sw.id);
    elements.push(createSwitchNode(sw, switches));
    elements.push(...createPortNodes(switchPorts, sw, switches));
  }
  elements.push(...links.map(createLinkEdge));
  return elements;
};

const createSwitchNode = (sw, switches) => {
  // Version de référence : taille, style et position identiques à l'ancienne version fonctionnelle
  const switchPosition = layoutPositions.value[`switch_${sw.id}`] || { x: switches.indexOf(sw) * 200 + 200, y: 100 };
  return {
    data: {
      id: `switch_${sw.id}`,
      label: sw.in_topology ? `${sw.model}\n${sw.mngt_IP}` : `${sw.model}\n${sw.mngt_IP}\n(outside this Topology)`,
      group: 'nodes',
      type: 'switch',
      inTopology: sw.in_topology
    },
    position: switchPosition,
    style: {
      'background-color': '#f0f0f0',
      'width': '120px',
      'height': '80px',
      'shape': 'roundrectangle',
      'text-valign': 'bottom',
      'text-halign': 'center',
      'text-margin-y': '10px',
      'text-wrap': 'wrap',
      'text-max-width': '100px',
      ...(sw.in_topology ? {} : { 'opacity': 0.5, 'border-style': 'dashed', 'border-width': '2px' })
    }
  };
};

const createPortNodes = (ports, sw, switches) => {
  // Version de référence : position identique à l'ancienne version fonctionnelle
  const switchIndex = switches.indexOf(sw);
  return ports.map(port => {
    const portPosition = layoutPositions.value[`port_${port.id}`] || {
      x: switchIndex * 200 + 200,
      y: ports.indexOf(port) * 50 + 100
    };
    return {
      data: {
        id: `port_${port.id}`,
        label: port.port_switch,
        // How confirmations name the port: "1/1/2 on OS6860E-24 (10.69.145.11)"
        fullName: `${port.port_switch} on ${sw.model} (${sw.mngt_IP})`,
        group: 'nodes',
        parent: `switch_${sw.id}`,
        type: 'port',
        inTopology: sw.in_topology
      },
      position: portPosition,
      style: {
        'background-color': '#fff',
        'shape': 'rectangle',
        'width': '20px',
        'height': '20px'
      }
    };
  });
};

// One undirected edge per Link
const createLinkEdge = (link) => ({
  data: {
    id: `link_${link.svlan}`,
    source: `port_${link.ports[0]}`,
    target: `port_${link.ports[1]}`,
    svlan: link.svlan,
    type: 'link',
    // Set only when a disconnect failed (cytoscape's [teardownError] selector needs it absent otherwise)
    ...(link.teardown_error ? { teardownError: link.teardown_error } : {})
  }
});

// --- Cytoscape setup ---
const handleSwitchContextMenu = (event) => {
  // Only Switches of the topology shown, and only if the server says we may work on it
  if (!mayWork.value || !event.target.data('inTopology')) return;

  const node = event.target;
  const nodeId = node.id();
  const switchId = nodeId.replace('switch_', '');

  switchToReleaseId.value = switchId;
};

const handleEdgeContextMenu = (event) => {
  // Any Link with an end in the topology, including one to a Switch outside it
  if (!mayWork.value) return;

  const edge = event.target;
  const teardownError = edge.data('teardownError');
  const ends = `${edge.source().data('fullName')} and ${edge.target().data('fullName')}`;
  confirm.value = {
    title: 'Disconnect this Link?',
    message: teardownError
      ? `Disconnecting the Link between ${ends} failed: ${plainMessage(teardownError)} Try again?`
      : `BLab removes the Link between ${ends} (SVLAN ${edge.data('svlan')}) from the backbones.`,
    label: 'Disconnect',
    danger: true,
    action: () => removeLink(edge.id()),
  };
};

const handlePortClick = (event) => {
  // Only Ports of the topology shown, and only if the server says we may work on it
  if (!mayWork.value || !event.target.data('inTopology')) return;

  const node = event.target;
  const isShiftPressed = event.originalEvent.shiftKey;
  if (isShiftPressed) {
    const portId = node.id();
    // Check if the clicked port is not already in the selectedPorts array
    if (!selectedPorts.value.includes(portId)) {
      selectedPorts.value.push(portId);

      // Add visual feedback for selected port
      node.style({
        'background-color': '#14b8a6',
        'border-width': '3px',
        'border-color': '#0f766e',
        'border-style': 'solid'
      });

      // If two ports are selected, create a link between them
      if (selectedPorts.value.length === 2) {
        const [source, target] = selectedPorts.value.map(id => cy.getElementById(id));
        confirm.value = {
          title: 'Connect these ports?',
          message: `BLab makes a Link between ${source.data('fullName')} and ${target.data('fullName')}.`,
          label: 'Connect',
          danger: false,
          action: () => createLink(source.id().replace('port_', ''), target.id().replace('port_', '')),
        };
      }
    }
  }
};

const createLink = async (sourcePortId, targetPortId) => {
  isLoading.value = true;
  try {
    const result = await portService.connect(sourcePortId, targetPortId);
    if (result.success) {
      toast.success('Link made.');
      updateTopology();
    } else {
      toast.error(`Couldn't connect the ports. ${result.message}`);
    }
  } finally {
    isLoading.value = false;
  }
};

// Function to clear port selection and visual feedback
const clearPortSelection = () => {
  // Reset visual style for all selected ports
  selectedPorts.value.forEach(portId => {
    if (cy) {
      const portNode = cy.getElementById(portId);
      if (portNode.length > 0) {
        portNode.style({
          'background-color': '#fff',
          'border-width': '0px',
          'border-color': '#000',
          'border-style': 'solid'
        });
      }
    }
  });
  // Clear the selectedPorts array
  selectedPorts.value = [];
};

const removeLink = async (edgeId) => {
  const edge = cy?.edges(`#${edgeId}`);
  if (!edge || edge.length === 0) return;  // gone meanwhile
  isLoading.value = true;
  try {
    const sourcePortId = edge.source().id().replace('port_', '');
    const targetPortId = edge.target().id().replace('port_', '');
    const result = await portService.disconnect(sourcePortId, targetPortId);
    if (result.success) {
      toast.success('Disconnecting the Link.');
      updateTopology();
    } else {
      toast.error(`Couldn't disconnect the Link. ${result.message}`);
    }
  } finally {
    isLoading.value = false;
  }
};

const updateTopology = async () => {
  if (!isDragging.value) {
    return fetchData(selectedTopologyOwnerId.value);
  }
  return null;
};

const saveLayoutPositions = debounce(() => {
  if (!cy) return;
  layoutPositions.value = {};
  cy.nodes().forEach(node => {
    layoutPositions.value[node.id()] = { x: node.position('x'), y: node.position('y') };
  });
  saveLayoutToStorage();
}, 300);

const setupCytoscape = () => {
  loadLayoutFromStorage();
  cy = cytoscape({
    container: cyContainer.value,
    style: [
      { selector: 'node', style: { 'label': 'data(label)', 'text-valign': 'bottom', 'text-halign': 'center', 'text-margin-y': '5px' } },
      { selector: 'edge', style: { 'width': 3, 'line-color': '#ccc' } },
      // A Link whose disconnect failed: shown again until a new attempt succeeds
      // Amber: a status, and red is for destructive actions only (tailwind.config.js)
      { selector: 'edge[teardownError]', style: { 'line-color': '#d97706', 'line-style': 'dashed',
        'label': 'disconnect failed', 'font-size': '10px', 'color': '#b45309', 'text-rotation': 'autorotate' } }
    ],
    layout: { name: 'preset' }
  });

  cy.on('dragfree', 'node', saveLayoutPositions);
  cy.on('cxttap', 'node[type="switch"]', handleSwitchContextMenu);
  cy.on('cxttap', 'edge', handleEdgeContextMenu);
  cy.on('tap', 'node[type="port"]', handlePortClick);

  // Prevent resetting position while moving nodes
  cy.on('position', 'node', (event) => {
    const node = event.target;
    layoutPositions.value[node.id()] = { x: node.position('x'), y: node.position('y') };
  });

  // Set isDragging flag
  cy.on('grab', 'node', () => {
    isDragging.value = true;
  });

  cy.on('free', 'node', () => {
    isDragging.value = false;
    updateTopology(); // Update topology after dragging is complete
  });
};

const loadLayoutFromStorage = () => {
  // Load positions for the current topology view
  const key = getLayoutKey();
  const savedLayout = localStorage.getItem(key);
  if (savedLayout) {
    layoutPositions.value = JSON.parse(savedLayout);
  } else {
    layoutPositions.value = {};
  }
};

const saveLayoutToStorage = () => {
  // Save positions for the current topology view
  const key = getLayoutKey();
  localStorage.setItem(key, JSON.stringify(layoutPositions.value));
};


const handleConfirm = () => {
  const action = confirm.value?.action;
  confirm.value = null;
  if (action) action();
  // Always clear port selection when closing confirmation dialog
  if (selectedPorts.value.length > 0) {
    clearPortSelection();
  }
};

const handleConfirmClose = () => {
  confirm.value = null;
  // Clear port selection when canceling
  if (selectedPorts.value.length > 0) {
    clearPortSelection();
  }
};
</script>
