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
              @release="releaseSwitch"
            />
        </div>
      </div>
    </div>
    <AlertDialog v-if="showAlert" :message="alertMessage" @close="showAlert = false" />
    <ConfirmationDialog v-if="showConfirm" :message="confirmMessage" @close="showConfirm = false" @confirm="handleConfirm" />
  </div>
</template>

<script setup>
import { ref, onMounted, watch, onBeforeUnmount } from 'vue';
import Navbar from '../components/Navbar.vue';
import Card from '../components/Card.vue';
import AlertDialog from '../components/AlertDialog.vue';
import ConfirmationDialog from '../components/ConfirmationDialog.vue';
import api from '../axiosConfig';
import { isAdmin } from '../auth';

const switches = ref([]);
const filteredSwitches = ref([]);
const searchText = ref('');
const hideReserved = ref(true);
const isLoading = ref(false);
const expandedItemId = ref(null);
const showAlert = ref(false);
const alertMessage = ref('');
const showConfirm = ref(false);
const confirmMessage = ref('');
const confirmAction = ref(null);
let reservedUsersCache = {};

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

const fetchReservations = async () => {
  try {
    const response = await api.get('list_reservation/');
    const reservations = response.data;
    await updateSwitchReservations(reservations);
    filterSwitches();
  } catch (error) {
    console.error(error);
  }
};

const updateSwitchReservations = async (reservations) => {
  for (const s of switches.value) {
    const matchingReservations = reservations.filter(r => r.switch === s.id);
    if (matchingReservations.length > 0) {
      s.reserved = true;
      s.reservedBy = await fetchReservedUsers(matchingReservations);
    }
  }
};

const fetchReservedUsers = async (reservations) => {
  const reservedUsers = [];
  await Promise.all(reservations.map(async reservation => {
    if (reservedUsersCache.hasOwnProperty(reservation.user)) {
      reservedUsers.push(reservedUsersCache[reservation.user]);
    } else {
      const user = await fetchUser(reservation.user);
      if (user) {
        reservedUsersCache[reservation.user] = user.username;
        reservedUsers.push(user.username);
      }
    }
  }));
  return reservedUsers;
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
        s.part_number.toLowerCase().includes(searchText.value.toLowerCase()) ||
        s.hardware_revision.toLowerCase().includes(searchText.value.toLowerCase()) ||
        s.serial_number.toLowerCase().includes(searchText.value.toLowerCase())
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
      if (isAdmin()) {
        confirmMessage.value = 'This switch is already reserved. Do you want to force reserve it?';
        confirmAction.value = async () => {
          await api.post('reserve/', { switch: switchId, confirmation: 1 });
          fetchSwitches();
        };
        showConfirm.value = true;
        return;
      } else {
        showAlertWithMessage(`Switch ${switchId} is already reserved.`);
        return;
      }
    }

    await api.post('reserve/', { switch: switchId, confirmation: 0 });
    fetchSwitches();
  } catch (error) {
    console.error(error);
  } finally {
    isLoading.value = false;
  }
};

const releaseSwitch = async (switchId) => {
  isLoading.value = true;
  try {
    const switchToRelease = switches.value.find(s => s.id === switchId);
    if (!switchToRelease) {
      console.error('Switch not found');
      return;
    }

    if (!switchToRelease.reserved) {
      showAlertWithMessage(`Switch ${switchId} is not reserved.`);
      return;
    }

    confirmMessage.value = 'Are you sure you want to release this switch?';
    confirmAction.value = async () => {
      await api.post('release/', { switch: switchId });
      fetchSwitches();
    };
    showConfirm.value = true;
  } catch (error) {
    console.error(error);
  } finally {
    isLoading.value = false;
  }
};

const handleConfirm = async () => {
  showConfirm.value = false;
  if (confirmAction.value) {
    try {
      await confirmAction.value();
    } catch (error) {
      showAlertWithMessage(error.response.data.detail || "Failed to complete action.");
    }
  }
};

const showAlertWithMessage = (message) => {
  alertMessage.value = message;
  showAlert.value = true;
};

watch([hideReserved, searchText], filterSwitches);

onMounted(fetchSwitches);

const fetchInterval = setInterval(fetchSwitches,  2 * 1000);

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