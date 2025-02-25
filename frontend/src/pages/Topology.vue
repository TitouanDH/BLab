<template>
  <div @contextmenu.prevent class="topology-page"> <!-- Add class here -->
    <Navbar />
    <div class="container mx-auto px-4 py-5"> <!-- Add container for buttons -->
      <TopologyControls @save="saveTopology" @load="loadTopology" />
    </div>
    <input type="file" @change="handleFileUpload" ref="fileInput" style="display: none"/>
    <LoadingOverlay v-if="isLoading" />
    <div ref="cyContainer" class="cy-container"></div>
    <HelpBall @toggle="toggleHelp" />
    <HelpPanel v-if="showHelp" />
    <AlertDialog v-if="showAlert" :message="alertMessage" @close="showAlert = false" />
    <ConfirmationDialog v-if="showConfirm" :message="confirmMessage" @close="showConfirm = false" @confirm="handleConfirm" />
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue';
import api from '../axiosConfig';
import cytoscape from 'cytoscape';
import Navbar from '../components/Navbar.vue';
import AlertDialog from '../components/AlertDialog.vue';
import ConfirmationDialog from '../components/ConfirmationDialog.vue';
import TopologyControls from '../components/TopologyControls.vue';
import LoadingOverlay from '../components/LoadingOverlay.vue';
import HelpBall from '../components/HelpBall.vue';
import HelpPanel from '../components/HelpPanel.vue';
import { debounce } from 'lodash';

const cyContainer = ref(null);
const showHelp = ref(false);
const isLoading = ref(false);
const fileInput = ref(null);
const hasReservations = ref(true);
const layoutPositions = ref({});
const showAlert = ref(false);
const alertMessage = ref('');
const showConfirm = ref(false);
const confirmMessage = ref('');
const confirmAction = ref(null);
const selectedPorts = ref([]); // Change selectedPorts to a ref
const isDragging = ref(false);
let interval = null;
let cy;

const toggleHelp = () => {
  showHelp.value = !showHelp.value;
};

const saveTopology = async () => {
  try {
    const response = await api.get('save_topology/');
    downloadJson(response.data, 'topology.json');
  } catch (error) {
    handleError('Failed to save topology.', error);
  }
};

const loadTopology = () => {
  if (hasReservations.value) {
    showAlertWithMessage('Release all your switches before loading a new topology.');
  } else {
    fileInput.value.click();
  }
};

const handleFileUpload = async (event) => {
  const file = event.target.files[0];
  if (file) {
    isLoading.value = true;
    const reader = new FileReader();
    reader.onload = async (e) => {
      try {
        const topologyData = JSON.parse(e.target.result);
        await uploadTopology(topologyData);
      } catch (parseError) {
        handleError('Failed to parse topology data.', parseError);
      } finally {
        isLoading.value = false;
      }
    };
    reader.readAsText(file);
  } else {
    isLoading.value = false;
  }
};

const uploadTopology = async (topologyData) => {
  try {
    const response = await api.post('load_topology/', { topology: topologyData });
    if (response.data.conflicts) {
      showAlertWithMessage('Conflicts detected: ' + JSON.stringify(response.data.conflicts));
    } else {
      updateTopology();
    }
  } catch (error) {
    handleUploadError(error);
  }
};

const handleUploadError = (error) => {
  if (error.response && error.response.status === 409) {
    showAlertWithMessage('Conflicts detected: ' + JSON.stringify(error.response.data.conflicts));
  } else {
    handleError('Failed to load topology.', error);
  }
};

const fetchData = async () => {
  try {
    const reservations = await fetchReservations();
    const reservedSwitchIds = getReservedSwitchIds(reservations);
    const switches = await fetchSwitches();
    const filteredSwitches = filterReservedSwitches(switches, reservedSwitchIds);
    const elements = await createElements(filteredSwitches);
    cy.json({ elements });
  } catch (error) {
    console.error('Error fetching data:', error);
  }
};

const fetchReservations = async () => {
  const response = await api.get('list_reservation/');
  hasReservations.value = response.data.some(reservation => reservation.user == localStorage.getItem('user'));
  return response.data;
};

const getReservedSwitchIds = (reservations) => {
  return reservations
    .filter(reservation => reservation.user == localStorage.getItem('user'))
    .map(reservation => reservation.switch);
};

const fetchSwitches = async () => {
  const response = await api.get('list_switch/');
  return response.data.switchs;
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
  return response.data;
};

const createSwitchNode = (sw, switchId, filteredSwitches) => {
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
  return ports.map(port => {
    const portPosition = layoutPositions.value[`port_${port.id}`] || { x: filteredSwitches.indexOf(filteredSwitches.find(sw => sw.id === switchId)) * 200 + 200, y: ports.indexOf(port) * 50 + 100 };
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
  return response.data.ports;
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

const saveLayoutPositions = debounce(() => {
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
  });
};

const handleSwitchContextMenu = (event) => {
  const node = event.target;
  const switchId = node.id().replace('switch_', '');
  const switchName = node.data('label');
  confirmMessage.value = `Do you want to release the switch ${switchName}?`;
  confirmAction.value = () => releaseSwitch(switchId);
  showConfirm.value = true;
};

const handleEdgeContextMenu = (event) => {
  const edgeId = event.target.id();
  confirmMessage.value = `Do you want to remove the link ${edgeId}?`;
  confirmAction.value = () => removeLink(edgeId);
  showConfirm.value = true;
};

const handlePortClick = (event) => {
  const node = event.target;
  const isShiftPressed = event.originalEvent.shiftKey;
  if (isShiftPressed) {
    const portId = node.id();
    // Check if the clicked port is not already in the selectedPorts array
    if (!selectedPorts.value.includes(portId)) {
      selectedPorts.value.push(portId);
      // If two ports are selected, create a link between them
      if (selectedPorts.value.length === 2) {
        const sourcePortId = selectedPorts.value[0].replace('port_', '');
        const targetPortId = selectedPorts.value[1].replace('port_', '');
        confirmMessage.value = `Do you want to create the link between port n°${sourcePortId} and n°${targetPortId}`;
        confirmAction.value = () => createLink(sourcePortId, targetPortId);
        showConfirm.value = true;
        // Clear the selectedPorts array
        selectedPorts.value = [];
      }
    }
  }
};


const releaseSwitch = async (switchId) => {
  isLoading.value = true;
  try {
    await api.post('release/', { switch: switchId });
    updateTopology();
  } catch (error) {
    handleError('Error releasing switch.', error);
  } finally {
    isLoading.value = false;
  }
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

const removeLink = async (edgeId) => {
  isLoading.value = true;
  try {
    const edge = cy.edges(`#${edgeId}`);
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
    fetchData();
  }
};

const resizeCyContainer = () => {
  if (cyContainer.value) {
    const navbarHeight = document.querySelector('nav').offsetHeight;
    const topologyControlHeight = document.querySelector('.topology-control').offsetHeight;
    cyContainer.value.style.height = `calc(100vh - ${navbarHeight + topologyControlHeight}px)`;
  }
};

const downloadJson = (json, filename) => {
  const blob = new Blob([JSON.stringify(json)], { type: 'application/json' });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(link.href);
};


const loadLayoutFromStorage = () => {
  const savedLayout = localStorage.getItem('topologyLayout');
  if (savedLayout) {
    layoutPositions.value = JSON.parse(savedLayout);
  }
};

const saveLayoutToStorage = () => {
  localStorage.setItem('topologyLayout', JSON.stringify(layoutPositions.value));
};

const showAlertWithMessage = (message) => {
  alertMessage.value = message;
  showAlert.value = true;
};

const handleError = (message, error) => {
  console.error(message, error);
  alertMessage.value = message + (error.response?.data?.detail ? `: ${error.response.data.detail}` : '');
  showAlert.value = true;
};

const handleConfirm = () => {
  if (confirmAction.value) {
    confirmAction.value();
  }
  showConfirm.value = false;
};

onMounted(() => {
  setupCytoscape();
  updateTopology();
  interval = setInterval(updateTopology, 2000);
  document.addEventListener('contextmenu', (event) => event.preventDefault()); // Disable right-click default behavior
});

onUnmounted(() => {
  clearInterval(interval);
  window.removeEventListener('resize', resizeCyContainer);
  saveLayoutPositions();
  document.removeEventListener('contextmenu', (event) => event.preventDefault()); // Remove event listener
});
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