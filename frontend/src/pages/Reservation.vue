<template>
  <div>
    <Navbar />
    <div v-if="isLoading" class="fixed inset-0 flex items-center justify-center z-50">
      <div class="loader"></div>
    </div>
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
            <Card 
              v-for="item in filteredSwitches" 
              :key="item.id" 
              :item="item" 
              :isLoading="isLoading" 
              :expandedItemId="expandedItemId"
              :toggleDetails="toggleDetails"
              @reserve="reserveSwitch"
            />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, watch, onBeforeUnmount } from 'vue';
import Navbar from '../components/Navbar.vue';
import Card from '../components/Card.vue';
import api from '../axiosConfig';

const switches = ref([]);
const filteredSwitches = ref([]);
const searchText = ref('');
const hideReserved = ref(true); // Check the box by default

const isLoading = ref(false);

const expandedItemId = ref(null);

const toggleDetails = (itemId) => {
  expandedItemId.value = expandedItemId.value === itemId ? null : itemId;
};

const fetchSwitches = async () => {
  try {
    const response = await api.get('list_switch/');
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
    const response = await api.get('list_reservation/');
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
    const response = await api.get(`list_user/${userId}/`);
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
        s.serial_number.toLowerCase().includes(searchText.value.toLowerCase()) // Include serial_number field
      )
    );
  });
};

const reserveSwitch = async (switchId) => {
  isLoading.value = true;
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
      
      const response = await api.post('reserve/', { switch: switchId, confirmation });
      
      console.log(response.data);
      fetchSwitches();
    } else {
      // Switch is not reserved, reserve it directly
      const response = await api.post('reserve/', { switch: switchId, confirmation: 0 });
      
      console.log(response.data);
      fetchSwitches();
    }
  } catch (error) {
    console.error(error);
  }
  finally {
    isLoading.value = false;
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

<style scoped>
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