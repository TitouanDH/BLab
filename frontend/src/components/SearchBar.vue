<template>
  <div class="flex items-center justify-between mb-4">
    <input v-model.trim="searchText" @input="$emit('input')" type="text" placeholder="Search" class="w-1/2 rounded-md border border-gray-300 shadow-sm focus:border-indigo-300 focus:ring focus:ring-indigo-200 focus:ring-opacity-50 px-4 py-2 mr-4">
    <label class="inline-flex items-center" @click="$emit('toggle')">
      <div :class="{ 'bg-indigo-600': !hideReserved, 'bg-gray-200': hideReserved }" class="relative rounded-full w-10 h-6 transition-colors duration-200 ease-in-out">
        <div :class="{ 'translate-x-4': !hideReserved }" class="absolute left-0 top-0 w-6 h-6 bg-white rounded-full shadow-md transform transition-transform duration-200 ease-in-out"></div>
      </div>
      <span class="ml-2 text-gray-700">Show reserved</span>
    </label>
  </div>
</template>

<script setup>
import { ref } from 'vue';

const searchText = ref('');
const hideReserved = ref(true);
</script>
