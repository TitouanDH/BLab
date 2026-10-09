<template>
  <div class="container mx-auto px-4 py-8">
    <SearchBar :searchText="searchText" @update:searchText="updateSearchText" @toggle="toggleHideReserved" :hideReserved="hideReserved" />
    <div v-if="!loaded" class="flex justify-center py-16 text-primary-700"><UiSpinner size="lg" /></div>
    <SwitchGrid v-else :switches="filteredSwitches" :isLoading="reserving || renewing" :expandedItemId="expandedItemId" @toggleDetails="toggleDetails" @reserve="reserveSwitch" @release="releaseSwitch" @renew="renewReservation" />

    <!-- Reserve dialog: choose the end date -->
    <UiModal v-if="showDatePicker" :title="`Reserve ${selectedSwitchName}`" @close="closeDatePicker">
      <p class="mb-4 text-gray-600">Choose when your Reservation ends (up to {{ MAX_RESERVATION_DAYS }} days from now).</p>
      <label for="end-date" class="mb-2 block font-medium text-gray-700">End date</label>
      <input id="end-date" v-model="selectedEndDate" type="date" :min="minDate" :max="isAdmin() ? null : maxDate" class="input" />
      <ul class="mt-4 space-y-1 text-xs text-gray-500">
        <li>Default: 7 days from today</li>
        <li>Maximum: {{ MAX_RESERVATION_DAYS }} days from now. Renew it to keep it longer, {{ RENEWAL_DAYS }} days at a time.</li>
        <li v-if="isAdmin()">As an admin, you may pick any later day: the Reservation is then an admin exception.</li>
      </ul>
      <template #actions>
        <UiButton @click="closeDatePicker">Cancel</UiButton>
        <UiButton variant="primary" :pending="reserving" @click="confirmReservation">Reserve</UiButton>
      </template>
    </UiModal>
    <ReleaseDialog v-if="switchToRelease !== null" :switchId="switchToRelease" @released="fetchSwitches" @close="switchToRelease = null" />
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue';
import SearchBar from '../components/SearchBar.vue';
import SwitchGrid from '../components/SwitchGrid.vue';
import ReleaseDialog from '../components/ReleaseDialog.vue';
import UiButton from '../components/ui/UiButton.vue';
import UiModal from '../components/ui/UiModal.vue';
import UiSpinner from '../components/ui/UiSpinner.vue';
import { switchService, reservationService, userService, topologyService } from '../utils/apiService.js';
import { getDefaultReservationDate, getMinReservationDate, getMaxReservationDate, reservationEnd } from '../utils/dateUtils.js';
import { MAX_RESERVATION_DAYS, RENEWAL_DAYS } from '../utils/constants.js';
import { getCurrentUserId, isAdmin } from '../auth.js';
import { toast } from '../composables/toast.js';
import { isUnreachable, usePoll } from '../composables/poll.js';

const switches = ref([]);
const filteredSwitches = ref([]);
const searchText = ref('');
const hideReserved = ref(true);
const loaded = ref(false);     // the first answer has come
const reserving = ref(false);
const expandedItemId = ref(null);
const showDatePicker = ref(false);
const selectedEndDate = ref('');
const selectedSwitchId = ref(null);
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

// Returns the API results, for the poll to tell whether BLab answered. BLab refusing (rather
// than not answering) is said once, in an error toast.
const fetchSwitches = async () => {
  const result = await loadSwitches();
  if (!result.success && !isUnreachable(result)) {
    toast.error(`Couldn't load the Switches. ${result.message}`, 'reservation-load');
  }
  return result;
};

const loadSwitches = async () => {
  const result = await switchService.getAll();
  if (!result.success) return result;
  switches.value = result.data.switchs.map(s => ({ ...s, reserved: false, reservedBy: null }));
  return fetchReservations(); // Ensure reservations are fetched after switches
};

const fetchReservations = async () => {
  const result = await reservationService.getAll();
  if (result.success) {
    await updateSwitchReservations(result.data);
    filterSwitches();
    loaded.value = true;
  }
  return result;
};

// The holders whose Topology is shared with the current user: they may Renew those Reservations too
const fetchSharedOwners = async () => {
  const result = await topologyService.getShared();
  if (!result.success) return new Set();
  return new Set(result.data.shared_with_me.map(share => String(share.owner_id)));
};

const updateSwitchReservations = async (reservations) => {
  const currentUserId = getCurrentUserId();
  const sharedOwners = await fetchSharedOwners();
  
  for (const s of switches.value) {
    const matchingReservations = reservations.filter(r => r.switch === s.id);
    if (matchingReservations.length > 0) {
      s.reserved = true;
      s.reservedBy = await fetchReservedUsers(matchingReservations);
      s.end_date = matchingReservations[0].end_date || null;
      s.renewals_left = matchingReservations[0].renewals_left;
      s.admin_exception = matchingReservations[0].admin_exception;
      // Check if current user is the owner of this reservation
      s.isOwner = matchingReservations.some(r => String(r.user) === String(currentUserId));
      s.mayRenew = s.isOwner || matchingReservations.some(r => sharedOwners.has(String(r.user)));
    } else {
      s.reserved = false;
      s.reservedBy = null;
      s.end_date = null;
      s.renewals_left = 0;
      s.admin_exception = false;
      s.isOwner = false;
      s.mayRenew = false;
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

const selectedSwitchName = computed(() => {
  const s = switches.value.find(x => x.id === selectedSwitchId.value);
  return s ? `${s.model} (${s.mngt_IP})` : 'this Switch';
});

const reserveSwitch = (switchId) => {
  const switchToReserve = switches.value.find(s => s.id === switchId);
  if (!switchToReserve || switchToReserve.reserved) return;
  selectedSwitchId.value = switchId;
  selectedEndDate.value = getDefaultEndDate();
  showDatePicker.value = true;
};

const closeDatePicker = () => {
  if (reserving.value) return;
  showDatePicker.value = false;
  selectedSwitchId.value = null;
  selectedEndDate.value = '';
};

const confirmReservation = async () => {
  // The end of the chosen day, within the limit the server enforces
  const endDateTime = selectedEndDate.value ? reservationEnd(selectedEndDate.value) : null;
  if (!endDateTime || isNaN(endDateTime.getTime())) {
    toast.error('Choose a valid end date.');
    return;
  }
  if (endDateTime < new Date()) {
    toast.error('The end date must be in the future.');
    return;
  }

  reserving.value = true;
  const result = await switchService.reserve(selectedSwitchId.value, endDateTime.toISOString());
  reserving.value = false;
  if (result.success) {
    toast.success(result.data?.detail || 'Reservation successful.');
    closeDatePicker();
    fetchSwitches();
  } else {
    // The server says why: Quarantine, Out of service, Cleanup in progress...
    toast.error(`Couldn't reserve this Switch. ${result.message}`);
  }
};

// Renewal: pushes the end date back, a limited number of times (the server decides)
const renewing = ref(false);
const renewReservation = async (switchId) => {
  if (renewing.value) return;
  renewing.value = true;
  const result = await switchService.renew(switchId);
  renewing.value = false;
  if (result.success) {
    toast.success(result.data.detail);
  } else {
    toast.error(`Couldn't Renew this Reservation. ${result.message}`);
  }
  fetchSwitches();
};

const releaseSwitch = (switchId) => {
  const switchObj = switches.value.find(s => s.id === switchId);
  if (switchObj?.reserved) switchToRelease.value = switchId;
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

usePoll(fetchSwitches, 2 * 1000);
</script>
