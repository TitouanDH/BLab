<template>
  <div class="fixed inset-0 flex items-center justify-center z-50">
    <div class="bg-white p-6 rounded-lg shadow-lg max-w-sm w-full">
      <div class="flex justify-between items-center">
        <h3 class="text-lg font-semibold text-gray-900">Error</h3>
        <button @click="$emit('close')" class="text-gray-500 hover:text-gray-700">
          <svg class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
          </svg>
        </button>
      </div>
      <div class="mt-4">
        <p class="text-gray-700">{{ message }}</p>
      </div>
      <div class="mt-6 flex justify-end">
        <button @click="$emit('close')" class="bg-teal-700 text-white px-4 py-2 rounded hover:bg-teal-600">Close</button>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  message: String
});
</script>

<style scoped>
/* Add any additional styles if needed */
</style>
