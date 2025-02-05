<template>
  <div>
    <Navbar />
    <LoadingOverlay v-if="isLoading" />
    <div class="container mx-auto px-4 py-8">
      <SearchBar :searchText="searchText" @input="filterSwitches" @toggle="toggleHideReserved" :hideReserved="hideReserved" />
      <SwitchGrid :switches="filteredSwitches" :isLoading="isLoading" :expandedItemId="expandedItemId" @toggleDetails="toggleDetails" @reserve="reserveSwitch" @release="releaseSwitch" />
    </div>
    <AlertDialog v-if="showAlert" :message="alertMessage" @close="showAlert = false" />
    <ConfirmationDialog v-if="showConfirm" :message="confirmMessage" @close="showConfirm = false" @confirm="handleConfirm" />
  </div>
</template>

<script setup>
import { ref, onMounted, watch, onBeforeUnmount } from 'vue';
import Navbar from '../components/Navbar.vue';
import AlertDialog from '../components/AlertDialog.vue';
import ConfirmationDialog from '../components/ConfirmationDialog.vue';
import LoadingOverlay from '../components/LoadingOverlay.vue';
import SearchBar from '../components/SearchBar.vue';
import SwitchGrid from '../components/SwitchGrid.vue';
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
    await fetchReservations(); // Ensure reservations are fetched after switches
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
    handleError('Failed to reserve switch.', error);
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
    handleError('Failed to release switch.', error);
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
      handleError('Failed to complete action.', error);
    }
  }
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

const toggleHideReserved = () => {
  hideReserved.value = !hideReserved.value;
  filterSwitches(); // Ensure switches are filtered when toggling hideReserved
};

watch([hideReserved, searchText], filterSwitches);

onMounted(fetchSwitches);

const fetchInterval = setInterval(fetchSwitches,  2 * 1000);

onBeforeUnmount(() => {
  clearInterval(fetchInterval);
});
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