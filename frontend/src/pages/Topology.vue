<template>
  <div>
    <Navbar />
    <div class="flex justify-end p-4">
      <button @click="saveTopology" class="bg-emerald-700 hover:bg-emerald-500 text-white font-bold py-2 px-4 rounded mr-2">
        Save Topology
      </button>
      <button @click="loadTopology" class="bg-cyan-700 hover:bg-cyan-500 text-white font-bold py-2 px-4 rounded">
        Load Topology
      </button>
    </div>
    <input type="file" @change="handleFileUpload" ref="fileInput" style="display: none"/>
    <div v-if="isLoading" class="fixed inset-0 flex items-center justify-center z-50">
      <div class="loader"></div>
    </div>
    <div ref="cyContainer" class="cy-container"></div>
    <div class="help-ball" @click="toggleHelp">
      <span class="help-text text-gray-700 text-2xl font-bold">?</span>
    </div>
    <div v-if="showHelp" class="help-panel bg-white border border-gray-300 rounded p-4 shadow-lg">
      <h3 class="text-lg font-semibold mb-2">How to Interact with the Topology</h3>
      <p class="mb-1">Right-click on a switch to release it.</p>
      <p class="mb-1">Right-click on a link to disconnect it.</p>
      <p class="mb-1">Shift-click on two ports to connect them.</p>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue';
import api from '../axiosConfig';
import cytoscape from 'cytoscape';
import Navbar from '../components/Navbar.vue';

const cyContainer = ref(null);
const showHelp = ref(false);
const isLoading = ref(false);
const fileInput = ref(null);

const hasReservations = ref(true);

// Reactive state for layout positions
let interval = null; 
let cy;
const layoutPositions = ref({});

// Toggle help panel visibility
const toggleHelp = () => {
  showHelp.value = !showHelp.value;
};

// Save topology to a file
const saveTopology = async () => {
  try {
    const response = await api.get('save_topology/');
    downloadJson(response.data, 'topology.json');
  } catch (error) {
    console.error('Failed to save topology:', error);
    alert('Failed to save topology.');
  }
};

// Modified loadTopology function
const loadTopology = () => {
  if (hasReservations.value) {
    alert('Release all your switches before loading a new topology.');
  } else {
    fileInput.value.click();
  }
};

// Handle file upload and send to server
const handleFileUpload = async (event) => {
  const file = event.target.files[0];
  if (file) {
    isLoading.value = true;
    const reader = new FileReader();
    reader.onload = async (e) => {
      try {
        const topologyData = JSON.parse(e.target.result);
        try {
          const response = await api.post('load_topology/', { topology: topologyData });
          if (response.data.conflicts) {
            alert('Conflicts detected: ' + JSON.stringify(response.data.conflicts));
          } else {
            updateTopology();
          }
        } catch (error) {
          console.error('Error uploading topology:', error);
          alert('Failed to load topology.');
        } finally {
          isLoading.value = false;
        }
      } catch (parseError) {
        console.error('Error parsing topology data:', parseError);
        alert('Failed to parse topology data.');
        isLoading.value = false;
      }
    };
    reader.readAsText(file);
  } else {
    isLoading.value = false;
  }
};

// Fetch data from API and update cytoscape
const fetchData = async () => {
  try {
    // Fetch reservations
    const reservationResponse = await api.get('list_reservation/');
    const reservations = reservationResponse.data;
    // Update hasReservations based on whether the current user has any active reservations
    hasReservations.value = reservations.some(reservation => reservation.user == localStorage.getItem('user'));

    const reservedSwitchIds = reservations
    .filter(reservation => reservation.user == localStorage.getItem('user'))
    .map(reservation => reservation.switch);

    // Fetch switches
    const switchResponse = await api.get('list_switch/');
    const switches = switchResponse.data.switchs;

    // Filter reserved switches
    const filteredSwitches = switches.filter(switchData => reservedSwitchIds.includes(switchData.id));

    const elements = [];
    const edges = [];

    for (const sw of filteredSwitches) {
      const switchId = sw.id;
      const portResponse = await api.get(`list_port/${switchId}/`);
      const ports = portResponse.data;

      // Add switch node
      let switchPosition = layoutPositions.value[`switch_${switchId}`] || { x: filteredSwitches.indexOf(sw) * 200 + 200, y: 100 };
      elements.push({
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
          'text-wrap': 'wrap', // Allow text wrapping
          'text-max-width': '100px' // Adjust the maximum width of the text
        }
      });

      // Add ports
      for (const port of ports) {
        let portPosition = layoutPositions.value[`port_${port.id}`] || { x: filteredSwitches.indexOf(sw) * 200 + 200, y: ports.indexOf(port) * 50 + 100 };
        elements.push({
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
        });

        // Find connected ports
        const allPortsResponse = await api.get(`list_port/`);
        const allPorts = allPortsResponse.data.ports;
        const connectedPorts = allPorts.filter(p => {
          const portSwitchId = p.switch;
          return p.svlan !== null && p.svlan === port.svlan && p.id !== port.id && filteredSwitches.some(sw => sw.id === portSwitchId);
        });

        // Create edges between connected ports
        for (const connectedPort of connectedPorts) {
          edges.push({
            data: {
              id: `port_${port.id}_to_port_${connectedPort.id}`,
              source: `port_${port.id}`,
              target: `port_${connectedPort.id}`,
              type: 'link'
            }
          });
        }
      }
    }

    // Add edges to the elements array
    elements.push(...edges);
    cy.json({ elements: elements });
  } catch (error) {
    console.error('Error fetching data:', error);
  }
};

// Save layout positions
const saveLayoutPositions = () => {
  layoutPositions.value = {};
  cy.nodes().forEach(node => {
    layoutPositions.value[node.id()] = node.position();
  });
};

// Lifecycle hooks for setup and teardown
onMounted(() => {
  setupCytoscape();
  updateTopology();
  interval = setInterval(updateTopology, 2000); // Update every 2 seconds
  if (fileInput.value) {
  }
});

onUnmounted(() => {
  clearInterval(interval);
  window.removeEventListener('resize', resizeCyContainer);
});

// Setup cytoscape instance
const setupCytoscape = () => {
  cy = cytoscape({
    container: cyContainer.value,
    style: [
      { selector: 'node', style: { 'label': 'data(label)', 'text-valign': 'bottom', 'text-halign': 'center', 'text-margin-y': '5px' } },
      { selector: 'edge', style: { 'width': 3, 'line-color': '#ccc', 'target-arrow-color': '#ccc', 'target-arrow-shape': 'triangle' } }
    ],
    layout: { name: 'preset' }
  });

  // Add context menu to switches (right-click)
  cy.on('cxttap', 'node[type="switch"]', (event) => {
    const node = event.target;
    const switchId = node.id().replace('switch_', '');
    const switchName = node.data('label');
    if (confirm(`Do you want to release the switch ${switchName}?`)) {
      releaseSwitch(switchId);
    }
  });
  // Add context menu to links (right-click)
  cy.on('cxttap', 'edge', (event) => {
    const edgeId = event.target.id();
    if (confirm(`Do you want to remove the link ${edgeId}?`)) {
      removeLink(edgeId);
    }
  });
  // Array to hold selected ports for link creation
  let selectedPorts = [];
  // Listen for shift + click events on ports
  cy.on('tap', 'node[type="port"]', (event) => {
    const node = event.target;
    const isShiftPressed = event.originalEvent.shiftKey;
    if (isShiftPressed) {
      const portId = node.id();
      // Check if the clicked port is not already in the selectedPorts array
      if (!selectedPorts.includes(portId)) {
        selectedPorts.push(portId);
        // If two ports are selected, create a link between them
        if (selectedPorts.length === 2) {
          const sourcePortId = selectedPorts[0].replace('port_', '');
          const targetPortId = selectedPorts[1].replace('port_', '');
          if (confirm(`Do you want to create the link between port n°${sourcePortId} and n°${targetPortId}`)) {
            createLink(sourcePortId, targetPortId);
          }
          // Clear the selectedPorts array
          selectedPorts = [];
        }
      }
    }
  });
};

const releaseSwitch = async (switchId) => {
  isLoading.value = true;
  try {
    // Release the switch via API
    await api.post('release/', { switch: switchId });
    console.log('Switch released successfully.');
    updateTopology();
  } catch (error) {
    console.error('Error releasing switch:', error);
  } finally {
    isLoading.value = false;
  }
};

// Function to create a link between ports
const createLink = async (sourcePortId, targetPortId) => {
  isLoading.value = true;
  try {
    await api.post('connect/', {
      portA: sourcePortId,
      portB: targetPortId
    });
    updateTopology();
  } catch (error) {
    console.error('Error connecting ports:', error);
    alert('Failed to connect ports.');
  } finally {
    isLoading.value = false;
  }
};

// Function to remove a link
const removeLink = async (edgeId) => {
  isLoading.value = true;
  try {
    const edge = cy.edges(`#${edgeId}`);
    const sourcePortId = edge.source().id().replace('port_', '');
    const targetPortId = edge.target().id().replace('port_', '');
    await api.post('disconnect/', {
      portA: sourcePortId,
      portB: targetPortId
    });
    updateTopology();
  } catch (error) {
    console.error('Error removing link:', error);
    alert('Failed to remove link.');
  } finally {
    isLoading.value = false;
  }
};

// Periodic update function
const updateTopology = () => {
  saveLayoutPositions();
  fetchData();
};

// Resize cytoscape container
const resizeCyContainer = () => {
  if (cyContainer.value) {
    const navbarHeight = document.querySelector('nav').offsetHeight;
    cyContainer.value.style.height = `calc(100vh - ${navbarHeight}px)`;
  }
};

// Utility to download JSON data as a file
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
</script>

<style scoped>
.cy-container {
  width: 100%;
  height: calc(100vh - 60px); /* Adjust height as needed */
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