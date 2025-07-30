<template>
  <div class="bg-white rounded-lg shadow-md p-4 flex flex-col justify-between">
    <div>
      <p class="text-lg font-semibold text-gray-900">{{ item.model }}</p>
      <p class="text-sm text-gray-600">{{ item.mngt_IP }}</p>
      <p class="text-sm text-gray-600">{{ item.console }}</p>
      <button @click="handleToggleDetails" class="text-sm text-gray-600 underline mt-2">View Details</button>
      <div v-show="expandedItemId === item.id" class="mt-2">
        <p class="text-sm text-gray-600">Part Number: {{ item.part_number }}</p>
        <p class="text-sm text-gray-600">Hardware Revision: {{ item.hardware_revision }}</p>
        <p class="text-sm text-gray-600">Serial Number: {{ item.serial_number }}</p>
      </div>
    </div>
    <div class="mt-4">
      <!-- Show Release button only if user is the owner, otherwise Reserve button or disabled if reserved by someone else -->
      <button 
        v-if="item.reserved && item.isOwner" 
        @click="releaseSwitch" 
        :disabled="isLoading" 
        class="px-4 py-2 bg-red-600 text-white font-semibold rounded-lg shadow-md hover:bg-red-500 focus:outline-none focus:bg-red-500"
      >
        Release
      </button>
      <button 
        v-else-if="!item.reserved" 
        @click="reserveSwitch" 
        :disabled="isLoading" 
        class="px-4 py-2 bg-indigo-600 text-white font-semibold rounded-lg shadow-md hover:bg-indigo-500 focus:outline-none focus:bg-indigo-500"
      >
        Reserve
      </button>
      <button 
        v-else 
        disabled 
        class="px-4 py-2 bg-gray-400 text-white font-semibold rounded-lg shadow-md cursor-not-allowed"
      >
        Reserved
      </button>
      
      <div v-if="item.reserved" class="mt-2">
        <p class="text-sm text-red-500">
          Reserved by: {{ Array.isArray(item.reservedBy) ? item.reservedBy.join(', ') : item.reservedBy }}
        </p>
        <p v-if="item.end_date" class="text-sm text-orange-600 mt-1">
          Expires: {{ formatDate(item.end_date) }}
        </p>
        <p v-else class="text-sm text-gray-500 mt-1">
          No expiration date set
        </p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { defineProps, defineEmits } from 'vue';

const props = defineProps({
  item: Object,
  isLoading: Boolean,
  expandedItemId: Number
});

const emit = defineEmits(['toggleDetails', 'reserve', 'release']);

const handleToggleDetails = () => {
  emit('toggleDetails', props.item.id);
};

const reserveSwitch = () => {
  emit('reserve', props.item.id);
};

const releaseSwitch = () => {
  emit('release', props.item.id);
};

function formatDate(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  const now = new Date();
  
  // Check if date is in the past
  const isPast = d < now;
  const daysDiff = Math.ceil((d - now) / (1000 * 60 * 60 * 24));
  
  let dateText = d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  
  if (isPast) {
    dateText += ' (EXPIRED)';
  } else if (daysDiff === 0) {
    dateText += ' (Today)';
  } else if (daysDiff === 1) {
    dateText += ' (Tomorrow)';
  } else if (daysDiff <= 7) {
    dateText += ` (${daysDiff} days)`;
  }
  
  return dateText;
}
</script>