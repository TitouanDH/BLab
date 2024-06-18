<template>
  <div class="bg-white rounded-lg shadow-md p-4 flex flex-col justify-between">
    <div>
      <p class="text-lg font-semibold text-gray-900">{{ item.model }}</p>
      <p class="text-sm text-gray-600">{{ item.mngt_IP }}</p>
      <p class="text-sm text-gray-600">{{ item.console }}</p>
      <button @click="toggleDetails(item.id)" class="text-sm text-gray-600 underline mt-2">View Details</button>
      <div v-show="expandedItemId === item.id" class="mt-2">
        <p class="text-sm text-gray-600">Part Number: {{ item.part_number }}</p>
        <p class="text-sm text-gray-600">Hardware Revision: {{ item.hardware_revision }}</p>
        <p class="text-sm text-gray-600">Serial Number: {{ item.serial_number }}</p>
      </div>
    </div>
    <div class="mt-4">
      <button @click="reserveSwitch" :disabled="isLoading" class="px-4 py-2 bg-indigo-600 text-white font-semibold rounded-lg shadow-md hover:bg-indigo-500 focus:outline-none focus:bg-indigo-500">Reserve</button>
      <p v-if="item.reserved" class="text-sm text-red-500 mt-2">Reserved by: {{ item.reservedBy }}</p>
    </div>
  </div>
</template>

<script setup>
import { emit } from 'vue';

const props = defineProps({
  item: Object,
  isLoading: Boolean,
  expandedItemId: Number,
  toggleDetails: Function
});

const reserveSwitch = () => {
  emit('reserve', props.item.id);
};
</script>