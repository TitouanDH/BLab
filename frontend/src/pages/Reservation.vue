<template>
  <div>
    <Navbar />
    <LoadingOverlay v-if="isLoading" />
    <div class="container mx-auto px-4 py-8">
      <SearchBar :searchText="searchText" @update:searchText="updateSearchText" @toggle="toggleHideReserved" :hideReserved="hideReserved" />
      <SwitchGrid :switches="filteredSwitches" :isLoading="isLoading" :expandedItemId="expandedItemId" @toggleDetails="toggleDetails" @reserve="reserveSwitch" @release="releaseSwitch" />
    </div>
    <AlertDialog v-if="showAlert" :message="alertMessage" @close="showAlert = false" />
    <ConfirmationDialog v-if="showConfirm" :message="confirmMessage" @close="showConfirm = false" @confirm="handleConfirm" />
    
    <!-- Reservation Date Picker Dialog -->
    <div v-if="showDatePicker" class="fixed inset-0 bg-black bg-opacity-40 flex items-center justify-center z-50">
      <div class="bg-white rounded-lg shadow-lg p-6 w-full max-w-md relative">
        <button @click="closeDatePicker" class="absolute top-2 right-2 text-gray-500 hover:text-gray-700 text-xl">&times;</button>
        
        <h3 class="text-lg font-bold mb-4">Select Reservation End Date</h3>
        <p class="text-sm text-gray-600 mb-6">Choose when your reservation should end (up to 21 days from now)</p>
        
        <div class="mb-6">
          <label class="block text-sm font-medium text-gray-700 mb-2">End Date:</label>
          <input 
            type="date" 
            v-model="selectedEndDate" 
            :min="minDate"
            :max="maxDate"
            class="w-full border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
          >
        </div>
        
        <div class="text-xs text-gray-500 mb-6 space-y-1">
          <p>• Default: 7 days from today</p>
          <p>• Maximum: 21 days from today</p>
        </div>
        
        <div class="flex justify-end space-x-3">
          <button 
            @click="closeDatePicker" 
            class="px-4 py-2 text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors"
          >
            Cancel
          </button>
          <button 
            @click="confirmReservation" 
            class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
          >
            Reserve Switch
          </button>
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
            @click="confirmRelease(true)" 
            class="w-full px-4 py-3 bg-red-600 text-white rounded-lg hover:bg-red-700 text-left transition-colors"
          >
            <div class="font-medium">Release & Cleanup</div>
            <div class="text-sm text-red-200">Disconnect all links and reset switch configuration (Recommended)</div>
          </button>
          
          <button 
            @click="confirmRelease(false)" 
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
import { ref, onMounted, watch, onBeforeUnmount } from 'vue';
import Navbar from '../components/Navbar.vue';
import AlertDialog from '../components/AlertDialog.vue';
import ConfirmationDialog from '../components/ConfirmationDialog.vue';
import LoadingOverlay from '../components/LoadingOverlay.vue';
import SearchBar from '../components/SearchBar.vue';
import SwitchGrid from '../components/SwitchGrid.vue';
import { switchService, reservationService, userService } from '../utils/apiService.js';
import { getDefaultReservationDate, getMinReservationDate, getMaxReservationDate, formatForInput } from '../utils/dateUtils.js';
import { handleApiError } from '../utils/errorHandler.js';
import { getCurrentUserId } from '../auth.js';

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
const showDatePicker = ref(false);
const selectedEndDate = ref('');
const selectedSwitchId = ref(null);
const showReleaseOptions = ref(false);
const switchToRelease = ref(null);
let reservedUsersCache = {};

const toggleDetails = (itemId) => {
  expandedItemId.value = expandedItemId.value === itemId ? null : itemId;
};

const getDefaultEndDate = () => {
  return getDefaultReservationDate();
};

const getMinDate = () => {
  return getMinReservationDate();
};

const getMaxDate = () => {
  return getMaxReservationDate();
};

const minDate = getMinDate();
const maxDate = getMaxDate();

const fetchSwitches = async () => {
  try {
    const result = await switchService.getAll();
    if (result.success) {
      switches.value = result.data.switchs.map(s => ({ ...s, reserved: false, reservedBy: null }));
      await fetchReservations(); // Ensure reservations are fetched after switches
    } else {
      handleError('Failed to fetch switches', { response: { data: { detail: result.message } } });
    }
  } catch (error) {
    console.error(error);
    handleError('Failed to fetch switches', error);
  }
};

const fetchReservations = async () => {
  try {
    const result = await reservationService.getAll();
    if (result.success) {
      const reservations = result.data;
      await updateSwitchReservations(reservations);
      filterSwitches();
    } else {
      handleError('Failed to fetch reservations', { response: { data: { detail: result.message } } });
    }
  } catch (error) {
    console.error(error);
    handleError('Failed to fetch reservations', error);
  }
};

const updateSwitchReservations = async (reservations) => {
  const currentUserId = getCurrentUserId();
  
  for (const s of switches.value) {
    const matchingReservations = reservations.filter(r => r.switch === s.id);
    if (matchingReservations.length > 0) {
      s.reserved = true;
      s.reservedBy = await fetchReservedUsers(matchingReservations);
      s.end_date = matchingReservations[0].end_date || null;
      // Check if current user is the owner of this reservation
      s.isOwner = matchingReservations.some(r => String(r.user) === String(currentUserId));
    } else {
      s.reserved = false;
      s.reservedBy = null;
      s.end_date = null;
      s.isOwner = false;
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
    const result = await userService.getById(userId);
    if (result.success) {
      return result.data;
    } else {
      console.error('Failed to fetch user:', result.message);
      return null;
    }
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
  const switchToReserve = switches.value.find(s => s.id === switchId);
  if (!switchToReserve) {
    console.error('Switch not found');
    return;
  }

  if (switchToReserve.reserved) {
    showAlertWithMessage(`Switch ${switchId} is already reserved.`);
    return;
  }

  selectedSwitchId.value = switchId;
  selectedEndDate.value = getDefaultEndDate();
  showDatePicker.value = true;
};

const closeDatePicker = () => {
  showDatePicker.value = false;
  selectedSwitchId.value = null;
  selectedEndDate.value = '';
};

const confirmReservation = async () => {
  if (!selectedSwitchId.value || !selectedEndDate.value) {
    showAlertWithMessage('Please select a valid end date.');
    return;
  }

  // Validate the selected date
  const endDate = new Date(selectedEndDate.value);
  if (isNaN(endDate.getTime())) {
    showAlertWithMessage('Invalid date selected. Please choose a valid date.');
    return;
  }

  // Check if date is in the future
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  if (endDate < today) {
    showAlertWithMessage('End date must be in the future.');
    return;
  }

  // SAVE the values BEFORE closing modal - this is the key fix!
  const savedSwitchId = selectedSwitchId.value;
  const savedEndDate = selectedEndDate.value;

  // Close modal immediately to prevent spam clicking and show loading
  closeDatePicker();
  isLoading.value = true;

  try {
    // Create end date time properly using the SAVED date
    const endDateTime = new Date(savedEndDate);
    endDateTime.setHours(23, 59, 59, 999); // Set to end of day
    
    if (isNaN(endDateTime.getTime())) {
      console.error('endDateTime is invalid:', endDateTime);
      throw new Error('Invalid date format');
    }
    
    const result = await switchService.reserve(savedSwitchId, endDateTime.toISOString());
    
    if (result.success) {
      fetchSwitches();
      showAlertWithMessage('Switch reserved successfully!');
    } else {
      throw new Error(result.message);
    }
  } catch (error) {
    console.error('Reservation error:', error);
    handleError('Failed to reserve switch.', error);
  } finally {
    isLoading.value = false;
  }
};

const releaseSwitch = async (switchId) => {
  const switchObj = switches.value.find(s => s.id === switchId);
  if (!switchObj) {
    console.error('Switch not found for ID:', switchId);
    return;
  }

  if (!switchObj.reserved) {
    showAlertWithMessage(`Switch ${switchId} is not reserved.`);
    return;
  }

  switchToRelease.value = switchId;
  showReleaseOptions.value = true;
};

const closeReleaseOptions = () => {
  showReleaseOptions.value = false;
  switchToRelease.value = null;
};

const confirmRelease = async (withCleanup) => {
  if (!switchToRelease.value) {
    console.error('No switch to release');
    return;
  }

  // Store the ID before closing the dialog and setting loading
  const switchIdToRelease = switchToRelease.value;
  closeReleaseOptions();
  isLoading.value = true;

  try {
    // Ensure switchId is a number
    const numericSwitchId = parseInt(switchIdToRelease, 10);
    
    if (isNaN(numericSwitchId)) {
      console.error('Switch ID is not a valid number:', switchIdToRelease);
      handleError('Invalid switch ID.', new Error('Switch ID is not a number'));
      return;
    }

    const result = await switchService.release(numericSwitchId, withCleanup);
    
    if (result.success) {
      fetchSwitches();
      showAlertWithMessage(result.data?.detail || 'Switch released successfully!');
    } else {
      throw new Error(result.message);
    }
  } catch (error) {
    console.error('Release error from Reservation.vue:', error);
    console.error('Error response:', error.response);
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

const updateSearchText = (newText) => {
  searchText.value = newText;
  filterSwitches();
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