<template>
  <div>
    <Navbar />
    <div class="container mx-auto px-4 py-8">
      <div class="flex items-center justify-between mb-4">
        <input v-model.trim="searchText" @input="filterSwitches" type="text" placeholder="Search" class="w-1/2 rounded-md border border-gray-300 shadow-sm focus:border-indigo-300 focus:ring focus:ring-indigo-200 focus:ring-opacity-50 px-4 py-2 mr-4">
        <label class="inline-flex items-center" @click="toggleHideReserved">
          <div :class="{ 'bg-indigo-600': !hideReserved, 'bg-gray-200': hideReserved }" class="relative rounded-full w-10 h-6 transition-colors duration-200 ease-in-out">
            <div :class="{ 'translate-x-4': !hideReserved }" class="absolute left-0 top-0 w-6 h-6 bg-white rounded-full shadow-md transform transition-transform duration-200 ease-in-out"></div>
          </div>
          <span class="ml-2 text-gray-700">Show reserved</span>
        </label>
      </div>
      <div>
        <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
          <div v-for="item in filteredSwitches" :key="item.id" class="bg-white rounded-lg shadow-md p-4 flex flex-col justify-between">
            <div>
              <p class="text-lg font-semibold text-gray-900">{{ item.model }}</p>
              <p class="text-sm text-gray-600">{{ item.mngt_IP }}</p>
              <p class="text-sm text-gray-600">{{ item.console }}</p>
              <button @click="toggleDetails(item.id)" class="text-sm text-gray-600 underline mt-2">View Details</button>
              <div v-show="expandedItemId === item.id" class="mt-2">
                <p class="text-sm text-gray-600">Part Number: {{ item.part_number }}</p>
                <p class="text-sm text-gray-600">Hardware Revision: {{ item.hardware_revision }}</p>
                <p class="text-sm text-gray-600">Serial Number: {{ item.serial_number }}</p>
                <p class="text-sm text-gray-600">FPGA Version: {{ item.fpga_version }}</p>
                <p class="text-sm text-gray-600">U-Boot Version: {{ item.uboot_version }}</p>
                <p class="text-sm text-gray-600">AOS Version: {{ item.aos_version }}</p>
              </div>
            </div>
            <div class="mt-4">
              <button @click="reserveSwitch(item.id)" class="px-4 py-2 bg-indigo-600 text-white font-semibold rounded-lg shadow-md hover:bg-indigo-500 focus:outline-none focus:bg-indigo-500">Reserve</button>
              <p v-if="item.reserved" class="text-sm text-red-500 mt-2">Reserved by: {{ item.reservedBy }}</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, watch, onBeforeUnmount } from 'vue';
import Navbar from '../components/Navbar.vue';
import axios from 'axios';

const switches = ref([]);
const filteredSwitches = ref([]);
const searchText = ref('');
const hideReserved = ref(true); // Check the box by default

const expandedItemId = ref(null);

const toggleDetails = (itemId) => {
  expandedItemId.value = expandedItemId.value === itemId ? null : itemId;
};

const fetchSwitches = async () => {
  try {
    const response = await axios.get('http://127.0.0.1:8000/api/list_switch/', {
      headers: {
        'Authorization': `Token ${localStorage.getItem('token')}`
      }
    });
    switches.value = response.data.switchs.map(s => ({ ...s, reserved: false, reservedBy: null }));
    fetchReservations();
  } catch (error) {
    console.error(error);
  }
};

// Define a variable to store fetched usernames
let reservedUsersCache = {};

// Function to fetch reservations
const fetchReservations = async () => {
  try {
    const response = await axios.get('http://127.0.0.1:8000/api/list_reservation/', {
      headers: {
        'Authorization': `Token ${localStorage.getItem('token')}`
      }
    });
    const reservations = response.data;
    switches.value.forEach(async s => {
      const matchingReservations = reservations.filter(r => r.switch === s.id);
      if (matchingReservations.length > 0) {
        s.reserved = true;
        const reservedUsers = [];
        await Promise.all(matchingReservations.map(async reservation => {
          // Check if the username is already in the cache
          if (reservedUsersCache.hasOwnProperty(reservation.user)) {
            reservedUsers.push(reservedUsersCache[reservation.user]);
          } else {
            // If not, fetch the username and store it in the cache
            const user = await fetchUser(reservation.user);
            if (user) {
              reservedUsersCache[reservation.user] = user.username;
              reservedUsers.push(user.username);
            }
          }
        }));
        s.reservedBy = reservedUsers;
      }
    });
    filterSwitches();
  } catch (error) {
    console.error(error);
  }
};

const fetchUser = async (userId) => {
  try {
    const response = await axios.get(`http://127.0.0.1:8000/api/list_user/${userId}/`, {
      headers: {
        'Authorization': `Token ${localStorage.getItem('token')}`
      }
    });
    return response.data;
  } catch (error) {
    console.error(error);
    return null;
  }
};

const filterSwitches = () => {
  filteredSwitches.value = switches.value.filter(s => {
    return (
      (!s.reserved || !hideReserved.value) &&
      (
        s.model.toLowerCase().includes(searchText.value.toLowerCase()) ||
        s.mngt_IP.toLowerCase().includes(searchText.value.toLowerCase()) ||
        s.console.toLowerCase().includes(searchText.value.toLowerCase()) ||
        s.part_number.toLowerCase().includes(searchText.value.toLowerCase()) || // Include part_number field
        s.hardware_revision.toLowerCase().includes(searchText.value.toLowerCase()) || // Include hardware_revision field
        s.serial_number.toLowerCase().includes(searchText.value.toLowerCase()) || // Include serial_number field
        s.fpga_version.toLowerCase().includes(searchText.value.toLowerCase()) || // Include fpga_version field
        s.uboot_version.toLowerCase().includes(searchText.value.toLowerCase()) || // Include uboot_version field
        s.aos_version.toLowerCase().includes(searchText.value.toLowerCase()) // Include aos_version field
      )
    );
  });
};

const reserveSwitch = async (switchId) => {
  try {
    const switchToReserve = switches.value.find(s => s.id === switchId);
    if (!switchToReserve) {
      console.error('Switch not found');
      return;
    }

    if (switchToReserve.reserved) {
      // Switch is already reserved, ask for confirmation
      const confirmed = confirm(`Switch ${switchId} is already reserved. Do you still want to reserve it?`);
      const confirmation = confirmed ? 1 : 0;
      
      const response = await axios.post('http://127.0.0.1:8000/api/reserve/', { switch: switchId, confirmation }, {
        headers: {
          'Authorization': `Token ${localStorage.getItem('token')}`
        }
      });
      
      console.log(response.data);
      fetchSwitches();
    } else {
      // Switch is not reserved, reserve it directly
      const response = await axios.post('http://127.0.0.1:8000/api/reserve/', { switch: switchId, confirmation: 0 }, {
        headers: {
          'Authorization': `Token ${localStorage.getItem('token')}`
        }
      });
      
      console.log(response.data);
      fetchSwitches();
    }
  } catch (error) {
    console.error(error);
  }
};

// Watch for changes in hideReserved and searchText
watch([hideReserved, searchText], () => {
  filterSwitches();
});

onMounted(fetchSwitches);

// Periodically fetch switches and reservations every 5 minutes
const fetchInterval = setInterval(fetchSwitches,  2 * 1000);

// Cleanup function to clear interval when component is unmounted
onBeforeUnmount(() => {
  clearInterval(fetchInterval);
});

const toggleHideReserved = () => {
  hideReserved.value = !hideReserved.value;
};
</script>
