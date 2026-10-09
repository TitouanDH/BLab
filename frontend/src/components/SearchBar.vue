<template>
  <div class="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
    <input v-model="localSearchText" type="search" placeholder="Search Switches" aria-label="Search Switches" class="input sm:w-1/2" />
    <label class="inline-flex cursor-pointer items-center" @click="$emit('toggle')">
      <div :class="hideReserved ? 'bg-gray-300' : 'bg-primary-600'" class="relative flex h-6 w-10 items-center rounded-full px-1 transition-colors duration-200 ease-linear">
        <span :class="hideReserved ? 'translate-x-0' : 'translate-x-4'" class="inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform duration-200 ease-linear"></span>
      </div>
      <span class="ml-2 text-sm text-gray-700">Show reserved Switches</span>
    </label>
  </div>
</template>

<script setup>
import { watch, ref } from 'vue';

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
