<template>
  <div class="flex items-center justify-between mb-4">
    <input :value="searchText" @input="$emit('update:searchText', $event.target.value)" type="text" placeholder="Search" class="w-1/2 rounded-md border border-gray-300 shadow-sm focus:border-indigo-300 focus:ring focus:ring-indigo-200 focus:ring-opacity-50 px-4 py-2 mr-4">
    <label class="inline-flex items-center" @click="$emit('toggle')">
      <div :class="{ 'bg-green-600': !hideReserved, 'bg-red-600': hideReserved }" class="relative rounded-full w-10 h-6 transition-colors duration-200 ease-linear flex items-center px-1">
        <span :class="{ 'translate-x-4': !hideReserved, 'translate-x-0': hideReserved }" class="inline-block w-4 h-4 transform bg-white rounded-full shadow transition-transform duration-200 ease-linear"></span>
      </div>
      <span class="ml-2 text-sm text-gray-700">Hide Reserved</span>
    </label>
  </div>
</template>

<script setup>
import { defineProps } from 'vue';

const props = defineProps({
  searchText: String,
  hideReserved: Boolean
});
</script>
