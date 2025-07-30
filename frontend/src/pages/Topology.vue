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
    <!-- Release Options Dialog -->
    <div v-if="showReleaseOptions" class="fixed inset-0 bg-black bg-opacity-40 flex items-center justify-center z-50">
      <div class="bg-white rounded-lg shadow-lg p-6 w-full max-w-md relative">
        <button @click="closeReleaseOptions" class="absolute top-2 right-2 text-gray-500 hover:text-gray-700 text-xl">&times;</button>
        
        <h3 class="text-lg font-bold mb-4">Release Switch</h3>
        <p class="text-sm text-gray-600 mb-6">Choose how you want to release this switch:</p>
        
        <div class="space-y-3">
          <button 
            @click="confirmReleaseTopology(true)" 
            class="w-full px-4 py-3 bg-red-600 text-white rounded-lg hover:bg-red-700 text-left transition-colors"
          >
            <div class="font-medium">Release & Cleanup</div>
            <div class="text-sm text-red-200">Disconnect all links and reset switch configuration (Recommended)</div>
          </button>
          
          <button 
            @click="confirmReleaseTopology(false)" 
            class="w-full px-4 py-3 bg-orange-600 text-white rounded-lg hover:bg-orange-700 text-left transition-colors"
          >
            <div class="font-medium">Release Only</div>
            <div class="text-sm text-orange-200">Disconnect links but keep switch configuration</div>
          </button>
          
          <button 
            @click="closeReleaseOptions" 
            class="w-full px-4 py-3 text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue';
import api from '../axiosConfig';
import cytoscape from 'cytoscape';
import Navbar from '../components/Navbar.vue';
import AlertDialog from '../components/AlertDialog.vue';
import ConfirmationDialog from '../components/ConfirmationDialog.vue';
import LoadingOverlay from '../components/LoadingOverlay.vue';
import HelpBall from '../components/HelpBall.vue';
import HelpPanel from '../components/HelpPanel.vue';
import { debounce } from 'lodash';

// --- State ---
const cyContainer = ref(null);
const showHelp = ref(false);
const isLoading = ref(false);
const fileInput = ref(null);
const hasReservations = ref(true);
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
const showReleaseOptions = ref(false);
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
  myUserId.value = localStorage.getItem('user') || '';
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
    const res = await api.get('list_shared_topologies/');
    const data = res.data || {};
    
    // Handle the new API response format
    topologiesSharedWithMe.value = data.shared_with_me || [];
    topologiesIShared.value = data.shared_by_me || [];
    
    // Keep backward compatibility
    sharedTopologies.value = [...topologiesSharedWithMe.value, ...topologiesIShared.value];
  } catch (e) {
    console.error('Error fetching shared topologies:', e);
    sharedTopologies.value = [];
    topologiesSharedWithMe.value = [];
    topologiesIShared.value = [];
  }
};

const fetchAvailableUsers = async () => {
  try {
    const res = await api.get('list_user/');
    // user is just a string id
    const userId = localStorage.getItem('user');
    availableUsers.value = (res.data?.users || []).filter(u => String(u.id) !== String(userId));
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
    await api.post('share_topology/', { target_username: userObj.username });
    alertMessage.value = 'Topology shared!';
    showAlert.value = true;
    shareTargetUserId.value = '';
    showSharePopup.value = false;
    fetchSharedTopologies();
  } catch (e) {
    alertMessage.value = e.response?.data?.detail || 'Failed to share topology.';
    showAlert.value = true;
  }
};

const unshareTopology = async (shareId) => {
  try {
    await api.delete(`unshare_topology/${shareId}/`);
    alertMessage.value = 'Topology unshared successfully!';
    showAlert.value = true;
    fetchSharedTopologies();
  } catch (e) {
    alertMessage.value = e.response?.data?.detail || 'Failed to unshare topology.';
    showAlert.value = true;
  }
};

// --- Cytoscape Logic ---
const fetchData = async (ownerId) => {
  try {
    // Utilise ownerId pour filtrer, que ce soit moi ou un autre
    const reservations = await fetchReservations();
    const reservedSwitchIds = getReservedSwitchIds(reservations, ownerId);
    const switches = await fetchSwitches();
    const filteredSwitches = filterReservedSwitches(switches, reservedSwitchIds);
    const elements = await createElements(filteredSwitches);
    if (cy) {
      cy.json({ elements });
      // Don't run layout automatically to preserve zoom
      cy.nodes().forEach(node => {
        const pos = layoutPositions.value[node.id()];
        if (pos) node.position(pos);
      });
    }
  } catch (error) {
    console.error('Error fetching data:', error);
  }
};

const fetchReservations = async () => {
  const response = await api.get('list_reservation/');
  // user est toujours un ID (number ou string), donc on prend la valeur brute
  const userId = localStorage.getItem('user');
  const reservations = response.data || [];
  hasReservations.value = reservations.some(reservation => String(reservation.user) === String(userId));
  return reservations;
};

const getReservedSwitchIds = (reservations, userId) => {
  return reservations
    .filter(reservation => String(reservation.user) === String(userId))
    .map(reservation => reservation.switch);
};

const fetchSwitches = async () => {
  const response = await api.get('list_switch/');
  return response.data?.switchs || [];
};

const filterReservedSwitches = (switches, reservedSwitchIds) => {
  return switches.filter(switchData => reservedSwitchIds.includes(switchData.id));
};

const createElements = async (filteredSwitches) => {
  const elements = [];
  const edges = [];
  for (const sw of filteredSwitches) {
    const switchId = sw.id;
    const ports = await fetchPorts(switchId);
    elements.push(createSwitchNode(sw, switchId, filteredSwitches));
    elements.push(...createPortNodes(ports, switchId, filteredSwitches));
    edges.push(...await createEdges(ports, filteredSwitches));
  }
  elements.push(...edges);
  return elements;
};

const fetchPorts = async (switchId) => {
  const response = await api.get(`list_port/${switchId}/`);
  return response.data || [];
};

const createSwitchNode = (sw, switchId, filteredSwitches) => {
  // Version de référence : taille, style et position identiques à l'ancienne version fonctionnelle
  const switchPosition = layoutPositions.value[`switch_${switchId}`] || { x: filteredSwitches.indexOf(sw) * 200 + 200, y: 100 };
  return {
    data: {
      id: `switch_${switchId}`,
      label: `${sw.model}\n${sw.mngt_IP}`,
      group: 'nodes',
      type: 'switch'
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
      'text-max-width': '100px'
    }
  };
};

const createPortNodes = (ports, switchId, filteredSwitches) => {
  // Version de référence : position identique à l'ancienne version fonctionnelle
  return ports.map(port => {
    const switchIndex = filteredSwitches.findIndex(sw => sw.id === switchId);
    const portPosition = layoutPositions.value[`port_${port.id}`] || { 
      x: switchIndex >= 0 ? switchIndex * 200 + 200 : 200, 
      y: ports.indexOf(port) * 50 + 100 
    };
    return {
      data: {
        id: `port_${port.id}`,
        label: port.port_switch,
        group: 'nodes',
        parent: `switch_${switchId}`,
        type: 'port'
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

const createEdges = async (ports, filteredSwitches) => {
  const edges = [];
  const allPorts = await fetchAllPorts();
  for (const port of ports) {
    const connectedPorts = findConnectedPorts(port, allPorts, filteredSwitches);
    edges.push(...createPortEdges(port, connectedPorts));
  }
  return edges;
};

const fetchAllPorts = async () => {
  const response = await api.get('list_port/');
  return response.data?.ports || [];
};

const findConnectedPorts = (port, allPorts, filteredSwitches) => {
  return allPorts.filter(p => {
    const portSwitchId = p.switch;
    return p.svlan !== null && p.svlan === port.svlan && p.id !== port.id && filteredSwitches.some(sw => sw.id === portSwitchId);
  });
};

const createPortEdges = (port, connectedPorts) => {
  return connectedPorts.map(connectedPort => ({
    data: {
      id: `port_${port.id}_to_port_${connectedPort.id}`,
      source: `port_${port.id}`,
      target: `port_${connectedPort.id}`,
      type: 'link'
    }
  }));
};

// --- Cytoscape setup ---
const handleSwitchContextMenu = (event) => {
  // Allow switch operations on own topology OR shared topologies
  const isOwnTopology = selectedTopologyOwnerId.value === myUserId.value;
  const isSharedTopology = topologiesSharedWithMe.value.some(share => 
    String(share.owner_id) === String(selectedTopologyOwnerId.value)
  );
  
  if (!isOwnTopology && !isSharedTopology) return;
  
  const node = event.target;
  const nodeId = node.id();
  const switchId = nodeId.replace('switch_', '');
  
  console.log('Switch context menu:', { 
    nodeId, 
    extractedSwitchId: switchId,
    numericSwitchId: parseInt(switchId, 10)
  });
  
  switchToReleaseId.value = switchId;
  showReleaseOptions.value = true;
};

const handleEdgeContextMenu = (event) => {
  // Allow edge operations on own topology OR shared topologies
  const isOwnTopology = selectedTopologyOwnerId.value === myUserId.value;
  const isSharedTopology = topologiesSharedWithMe.value.some(share => 
    String(share.owner_id) === String(selectedTopologyOwnerId.value)
  );
  
  if (!isOwnTopology && !isSharedTopology) return;
  
  const edgeId = event.target.id();
  confirmMessage.value = `Do you want to remove the link ${edgeId}?`;
  confirmAction.value = () => removeLink(edgeId);
  showConfirm.value = true;
};

const handlePortClick = (event) => {
  // Allow port operations on own topology OR shared topologies
  const isOwnTopology = selectedTopologyOwnerId.value === myUserId.value;
  const isSharedTopology = topologiesSharedWithMe.value.some(share => 
    String(share.owner_id) === String(selectedTopologyOwnerId.value)
  );
  
  if (!isOwnTopology && !isSharedTopology) return;
  
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

const releaseSwitch = async (switchId, withCleanup = false) => {
  console.log('releaseSwitch called with:', { switchId, withCleanup, type: typeof switchId });
  
  if (!switchId) {
    console.error('Switch ID is null or undefined');
    handleError('Switch ID is missing.', new Error('Switch ID is null'));
    return;
  }
  
  isLoading.value = true;
  try {
    // Ensure switchId is a number (convert string to int if needed)
    const numericSwitchId = parseInt(switchId, 10);
    
    if (isNaN(numericSwitchId)) {
      console.error('Switch ID is not a valid number:', switchId);
      handleError('Invalid switch ID.', new Error('Switch ID is not a number'));
      return;
    }
    
    console.log('Releasing switch:', { 
      originalId: switchId, 
      numericId: numericSwitchId, 
      withCleanup 
    });
    
    // Use the same format as the old working code
    const requestData = {
      switch: numericSwitchId,
      cleanup: withCleanup
    };
    
    console.log('Sending request data:', requestData);
    
    const response = await api.post('release/', requestData);
    updateTopology(); // Ensure the topology is updated correctly
    alertMessage.value = response.data.detail || 'Switch released successfully!';
    showAlert.value = true;
  } catch (error) {
    console.error('Release error:', error);
    console.error('Error response:', error.response);
    handleError('Error releasing switch.', error);
  } finally {
    isLoading.value = false;
  }
};

const closeReleaseOptions = () => {
  showReleaseOptions.value = false;
  switchToReleaseId.value = null;
};

const confirmReleaseTopology = async (withCleanup) => {
  console.log('confirmReleaseTopology called with:', { 
    switchToReleaseId: switchToReleaseId.value, 
    withCleanup 
  });
  
  if (!switchToReleaseId.value) {
    console.error('No switch ID to release');
    return;
  }
  
  // Store the ID before closing the dialog
  const switchIdToRelease = switchToReleaseId.value;
  closeReleaseOptions();
  await releaseSwitch(switchIdToRelease, withCleanup);
};

const createLink = async (sourcePortId, targetPortId) => {
  isLoading.value = true;
  try {
    await api.post('connect/', { portA: sourcePortId, portB: targetPortId });
    updateTopology();
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
    await api.post('disconnect/', { portA: sourcePortId, portB: targetPortId });
    updateTopology();
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
      { selector: 'edge', style: { 'width': 3, 'line-color': '#ccc', 'target-arrow-color': '#ccc', 'target-arrow-shape': 'triangle' } }
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
  console.log('File upload triggered:', event.target.files);
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