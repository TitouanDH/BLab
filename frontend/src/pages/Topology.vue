<template>
  <div @contextmenu.prevent class="topology-page">
    <Navbar />
    <div class="container mx-auto px-4 py-5 flex items-center">
      <!-- Rolling select for topology views -->
      <select v-model="selectedTopologyOwnerId" @change="onTopologyViewChange" class="border rounded px-2 py-1 mr-4">
        <option :value="myUserId">My Topology View</option>
        <option v-for="share in topologiesSharedWithMe" :key="share.owner_id" :value="share.owner_id">
          {{ share.owner_username }} Topology View
        </option>
      </select>
      <button @click="showSharePopup = true" class="bg-blue-600 text-white px-3 py-1 rounded ml-auto">
        Share Topology
      </button>
    </div>
    <input type="file" @change="handleFileUpload" ref="fileInput" style="display: none"/>
    <LoadingOverlay v-if="isLoading" />
    <div ref="cyContainer" class="cy-container"></div>
    <HelpBall @toggle="toggleHelp" />
    <HelpPanel v-if="showHelp" />
    <AlertDialog v-if="showAlert" :message="alertMessage" @close="showAlert = false" />
    <ConfirmationDialog v-if="showConfirm" :message="confirmMessage" @close="handleConfirmClose" @confirm="handleConfirm" />

    <!-- Share Topology Popup -->
    <div v-if="showSharePopup" class="fixed inset-0 bg-black bg-opacity-40 flex items-center justify-center z-50">
      <div class="bg-white rounded-lg shadow-lg p-6 w-full max-w-2xl flex flex-col relative">
        <button @click="showSharePopup = false" class="absolute top-2 right-2 text-gray-500 hover:text-gray-700 text-xl">&times;</button>
        
        <!-- Top Section: Share with new user -->
        <div class="mb-6">
          <h3 class="text-lg font-bold mb-4">Share Topology</h3>
          <h4 class="font-semibold mb-3">Share my topology with:</h4>
          <div class="flex items-center space-x-3">
            <select v-model="shareTargetUserId" class="border border-gray-300 rounded-lg px-3 py-2 flex-grow focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500">
              <option disabled value="">Select a user to share with</option>
              <option v-for="user in availableUsers" :key="user.id" :value="user.id">
                {{ user.username }}
              </option>
            </select>
            <button @click="shareTopology" :disabled="!shareTargetUserId" class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors">Share</button>
          </div>
        </div>

        <!-- Bottom Section: Manage Shares -->
        <div class="grid grid-cols-2 gap-6">
          <!-- Left: Shared with me -->
          <div>
            <h5 class="font-semibold mb-3 text-gray-700 border-b border-gray-200 pb-2">Shared with me</h5>
            <ul v-if="topologiesSharedWithMe.length > 0" class="space-y-2">
              <li v-for="share in topologiesSharedWithMe" :key="share.id" class="flex justify-between items-center bg-gray-50 p-3 rounded-lg">
                <span class="text-gray-700">{{ share.owner_username }}</span>
                <button @click="unshareTopology(share.id)" class="text-red-500 hover:text-red-700 font-bold text-xl transition-colors">&times;</button>
              </li>
            </ul>
            <p v-else class="text-gray-500 text-sm">No topologies have been shared with you.</p>
          </div>

          <!-- Right: Shared with others -->
          <div>
            <h5 class="font-semibold mb-3 text-gray-700 border-b border-gray-200 pb-2">Shared with others</h5>
            <ul v-if="topologiesIShared.length > 0" class="space-y-2">
              <li v-for="share in topologiesIShared" :key="share.id" class="flex justify-between items-center bg-gray-50 p-3 rounded-lg">
                <span class="text-gray-700">{{ share.target_username }}</span>
                <button @click="unshareTopology(share.id)" class="text-red-500 hover:text-red-700 font-bold text-xl transition-colors">&times;</button>
              </li>
            </ul>
            <p v-else class="text-gray-500 text-sm">You haven't shared your topology with anyone.</p>
          </div>
        </div>
      </div>
    </div>
    <ReleaseDialog v-if="switchToReleaseId !== null" :switchId="switchToReleaseId" @released="updateTopology" @close="switchToReleaseId = null" />
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue';
import cytoscape from 'cytoscape';
import Navbar from '../components/Navbar.vue';
import AlertDialog from '../components/AlertDialog.vue';
import ConfirmationDialog from '../components/ConfirmationDialog.vue';
import LoadingOverlay from '../components/LoadingOverlay.vue';
import HelpBall from '../components/HelpBall.vue';
import HelpPanel from '../components/HelpPanel.vue';
import ReleaseDialog from '../components/ReleaseDialog.vue';
import { debounce } from 'lodash';
import { portService, userService, topologyService } from '../utils/apiService.js';
import { handleApiError } from '../utils/errorHandler.js';
import { getCurrentUserId } from '../auth.js';

// --- State ---
const cyContainer = ref(null);
const showHelp = ref(false);
const isLoading = ref(false);
const fileInput = ref(null);
const mayWork = ref(false); // whether we may connect, disconnect and release in the topology shown
const layoutPositions = ref({}); // Will now store per-topology layouts
const showAlert = ref(false);
const alertMessage = ref('');
const showConfirm = ref(false);
const confirmMessage = ref('');
const confirmAction = ref(null);
const isDragging = ref(false);
let interval = null;
let cy;

// Sharing state
const showSharePopup = ref(false);
const sharedTopologies = ref([]);
const topologiesSharedWithMe = ref([]);
const topologiesIShared = ref([]);
const availableUsers = ref([]);
const shareTargetUserId = ref('');
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
  selectedTopologyOwnerId.value = myUserId.value;

  await fetchSharedTopologies();
  await fetchAvailableUsers();
  setupCytoscape();
  setTimeout(async () => {
    await fetchData(myUserId.value);
  }, 0);
  interval = setInterval(() => {
    updateTopology();
  }, 2000);
  document.addEventListener('contextmenu', preventContext);
});

onUnmounted(() => {
  clearInterval(interval);
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
      
      // Keep backward compatibility
      sharedTopologies.value = [...topologiesSharedWithMe.value, ...topologiesIShared.value];
    } else {
      console.error('Error fetching shared topologies:', result.message);
      sharedTopologies.value = [];
      topologiesSharedWithMe.value = [];
      topologiesIShared.value = [];
    }
  } catch (e) {
    console.error('Error fetching shared topologies:', e);
    sharedTopologies.value = [];
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
  if (!shareTargetUserId.value) return;
  try {
    const userObj = availableUsers.value.find(u => u.id === shareTargetUserId.value);
    if (!userObj) {
      alertMessage.value = 'Selected user not found.';
      showAlert.value = true;
      return;
    }
    
    const result = await topologyService.share(userObj.username);
    if (result.success) {
      alertMessage.value = 'Topology shared!';
      showAlert.value = true;
      shareTargetUserId.value = '';
      showSharePopup.value = false;
      fetchSharedTopologies();
    } else {
      alertMessage.value = result.message || 'Failed to share topology.';
      showAlert.value = true;
    }
  } catch (e) {
    alertMessage.value = 'Failed to share topology.';
    showAlert.value = true;
  }
};

const unshareTopology = async (shareId) => {
  try {
    const result = await topologyService.unshare(shareId);
    if (result.success) {
      alertMessage.value = 'Topology unshared successfully!';
      showAlert.value = true;
      fetchSharedTopologies();
    } else {
      alertMessage.value = result.message || 'Failed to unshare topology.';
      showAlert.value = true;
    }
  } catch (e) {
    alertMessage.value = 'Failed to unshare topology.';
    showAlert.value = true;
  }
};

// --- Cytoscape Logic ---
// The server owns the Topology: its Switches, their Ports and every Link. This page only draws it.
const fetchData = async (ownerId) => {
  const result = await topologyService.get(ownerId);
  // A late reply for a topology we have since switched away from
  if (String(ownerId) !== String(selectedTopologyOwnerId.value)) return;
  if (!result.success) {
    console.error('Failed to fetch topology:', result.message);
    if (String(ownerId) !== String(myUserId.value) && [403, 404].includes(result.status)) {
      // The topology is no longer shared with us: back to our own
      selectedTopologyOwnerId.value = myUserId.value;
      alertMessage.value = 'This topology is no longer shared with you.';
      showAlert.value = true;
      await fetchSharedTopologies();
      await onTopologyViewChange();
    }
    return;
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
      label: sw.in_topology ? `${sw.model}\n${sw.mngt_IP}` : `${sw.model}\n${sw.mngt_IP}\n(outside this topology)`,
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
  
  const edgeId = event.target.id();
  const svlan = event.target.data('svlan');
  const teardownError = event.target.data('teardownError');
  confirmMessage.value = teardownError
    ? `Disconnecting the link on SVLAN ${svlan} failed: ${teardownError} Try again?`
    : `Do you want to remove the link on SVLAN ${svlan}?`;
  confirmAction.value = () => removeLink(edgeId);
  showConfirm.value = true;
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
        'background-color': '#4CAF50',
        'border-width': '3px',
        'border-color': '#2E7D32',
        'border-style': 'solid'
      });
      
      // If two ports are selected, create a link between them
      if (selectedPorts.value.length === 2) {
        const sourcePortId = selectedPorts.value[0].replace('port_', '');
        const targetPortId = selectedPorts.value[1].replace('port_', '');
        confirmMessage.value = `Do you want to create the link between port n°${sourcePortId} and n°${targetPortId}`;
        confirmAction.value = () => {
          createLink(sourcePortId, targetPortId);
          clearPortSelection();
        };
        showConfirm.value = true;
      }
    }
  }
};

const createLink = async (sourcePortId, targetPortId) => {
  isLoading.value = true;
  try {
    const result = await portService.connect(sourcePortId, targetPortId);
    if (result.success) {
      updateTopology();
    } else {
      handleError('Failed to connect ports.', { response: { data: { detail: result.message } } });
    }
  } catch (error) {
    handleError('Failed to connect ports.', error);
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
  isLoading.value = true;
  try {
    if (!cy) {
      throw new Error('Cytoscape not initialized');
    }
    const edge = cy.edges(`#${edgeId}`);
    if (edge.length === 0) {
      throw new Error('Edge not found');
    }
    const sourcePortId = edge.source().id().replace('port_', '');
    const targetPortId = edge.target().id().replace('port_', '');
    
    const result = await portService.disconnect(sourcePortId, targetPortId);
    if (result.success) {
      updateTopology();
    } else {
      handleError('Failed to remove link.', { response: { data: { detail: result.message } } });
    }
  } catch (error) {
    handleError('Failed to remove link.', error);
  } finally {
    isLoading.value = false;
  }
};

const updateTopology = async () => {
  if (!isDragging.value) {
    await fetchData(selectedTopologyOwnerId.value);
  }
};

const saveLayoutPositions = debounce(() => {
  if (!cy) return;
  layoutPositions.value = {};
  cy.nodes().forEach(node => {
    layoutPositions.value[node.id()] = { x: node.position('x'), y: node.position('y') };
  });
  saveLayoutToStorage();
}, 300);

const handleError = (message, error) => {
  console.error(message, error);
  alertMessage.value = message + (error.response?.data?.detail ? `: ${error.response.data.detail}` : '');
  showAlert.value = true;
};

const setupCytoscape = () => {
  loadLayoutFromStorage();
  cy = cytoscape({
    container: cyContainer.value,
    style: [
      { selector: 'node', style: { 'label': 'data(label)', 'text-valign': 'bottom', 'text-halign': 'center', 'text-margin-y': '5px' } },
      { selector: 'edge', style: { 'width': 3, 'line-color': '#ccc' } },
      // A Link whose disconnect failed: shown again until a new attempt succeeds
      { selector: 'edge[teardownError]', style: { 'line-color': '#e53935', 'line-style': 'dashed',
        'label': 'disconnect failed', 'font-size': '10px', 'color': '#e53935', 'text-rotation': 'autorotate' } }
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
  if (confirmAction.value) {
    confirmAction.value();
  }
  showConfirm.value = false;
  // Always clear port selection when closing confirmation dialog
  if (selectedPorts.value.length > 0) {
    clearPortSelection();
  }
};

const handleConfirmClose = () => {
  showConfirm.value = false;
  // Clear port selection when canceling
  if (selectedPorts.value.length > 0) {
    clearPortSelection();
  }
};

const handleFileUpload = (event) => {
  // Placeholder for file upload functionality
  // TODO: Implement file upload logic
};
</script>

<style scoped>
.topology-page {
  height: 100vh; /* Add this line to set the height of the parent div */
  overflow: hidden; /* Add this line to avoid scrollbar */
}

.cy-container {
  width: 100%;
  height: calc(100vh - 120px); /* Adjust height as needed */
  overflow: hidden; /* Add this line to avoid scrollbar */
}

.help-ball {
  position: fixed;
  bottom: 20px;
  right: 20px;
  width: 40px;
  height: 40px;
  background-color: #fff;
  border-radius: 50%;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
  cursor: pointer;
  display: flex;
  justify-content: center;
  align-items: center;
}

.help-ball:hover {
  background-color: #f0f0f0;
}

.help-text {
  font-size: 24px;
  color: #333;
}

.help-panel {
  position: fixed;
  bottom: 20px;
  right: 80px;
  background-color: #fff;
  border: 1px solid #ccc;
  border-radius: 4px;
  padding: 10px;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
  z-index: 999;
}

.help-panel h3 {
  margin-top: 0;
  margin-bottom: 10px;
}

.help-panel p {
  margin: 5px 0;
}

.loader {
  border: 2px solid #f3f3f3;
  border-radius: 50%;
  border-top: 2px solid #3498db;
  width: 50px;
  height: 50px;
  animation: spin 2s linear infinite;
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}
</style>