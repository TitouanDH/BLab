<template>
  <div class="fixed inset-0 bg-black bg-opacity-40 flex items-center justify-center z-50">
    <div class="bg-white rounded-lg shadow-lg p-6 max-w-md w-full relative">
      <button @click="$emit('close')" class="absolute top-2 right-2 text-gray-500 hover:text-gray-700 text-xl">&times;</button>
      
      <h3 class="text-lg font-bold mb-4 text-gray-900">Notification</h3>
      
      <div class="mb-6">
        <p class="text-gray-700">{{ message }}</p>
      </div>
      
      <div class="flex justify-end">
        <button @click="$emit('close')" class="px-4 py-2 bg-teal-600 text-white rounded-lg hover:bg-teal-700 transition-colors">Close</button>
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
