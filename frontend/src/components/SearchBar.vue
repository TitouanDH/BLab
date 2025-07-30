<template>
  <div class="flex items-center justify-between mb-4">
    <input v-model="localSearchText" type="text" placeholder="Search switches..." class="w-1/2 rounded-md border border-gray-300 shadow-sm focus:border-teal-300 focus:ring focus:ring-teal-200 focus:ring-opacity-50 px-4 py-2 mr-4">
    <label class="inline-flex items-center cursor-pointer" @click="$emit('toggle')">
      <div :class="{ 'bg-teal-600': !hideReserved, 'bg-gray-400': hideReserved }" class="relative rounded-full w-10 h-6 transition-colors duration-200 ease-linear flex items-center px-1">
        <span :class="{ 'translate-x-4': !hideReserved, 'translate-x-0': hideReserved }" class="inline-block w-4 h-4 transform bg-white rounded-full shadow transition-transform duration-200 ease-linear"></span>
      </div>
      <span class="ml-2 text-sm text-gray-700">Show Reserved</span>
    </label>
  </div>
</template>

<script setup>
import { defineProps, defineEmits, watch, ref } from 'vue';

const props = defineProps({
  searchText: String,
  hideReserved: Boolean
});

const emit = defineEmits(['update:searchText', 'toggle']);

const localSearchText = ref(props.searchText);

watch(localSearchText, (newValue) => {
  emit('update:searchText', newValue);
});
</script>
