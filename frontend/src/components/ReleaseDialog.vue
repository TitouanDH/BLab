<template>
  <!-- Release Options Dialog -->
  <div v-if="choosing" class="fixed inset-0 bg-black bg-opacity-40 flex items-center justify-center z-50">
    <div class="bg-white rounded-lg shadow-lg p-6 w-full max-w-md relative">
      <button @click="$emit('close')" class="absolute top-2 right-2 text-gray-500 hover:text-gray-700 text-xl">&times;</button>

      <h3 class="text-lg font-bold mb-4">Release Switch</h3>
      <p class="text-sm text-gray-600 mb-6">Choose how you want to release this switch:</p>

      <div class="space-y-3">
        <button
          @click="release(true)"
          class="w-full px-4 py-3 bg-red-600 text-white rounded-lg hover:bg-red-700 text-left transition-colors"
        >
          <div class="font-medium">Release & Cleanup</div>
          <div class="text-sm text-red-200">Disconnect all links, restore the init config and reboot (Recommended)</div>
        </button>

        <button
          @click="release(false)"
          class="w-full px-4 py-3 bg-orange-600 text-white rounded-lg hover:bg-orange-700 text-left transition-colors"
        >
          <div class="font-medium">Release Only</div>
          <div class="text-sm text-orange-200">Disconnect links but keep switch configuration</div>
        </button>

        <button
          @click="$emit('close')"
          class="w-full px-4 py-3 text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors"
        >
          Cancel
        </button>
      </div>
    </div>
  </div>
  <LoadingOverlay v-if="releasing" />
  <AlertDialog v-if="outcomeMessage" :message="outcomeMessage" @close="$emit('close')" />
</template>

<script setup>
// Releasing a Switch (see CONTEXT.md): asks whether to Clean it up, releases it, and
// shows the outcome. Emits "released" once the Reservation has ended, "close" when done.
import { ref } from 'vue';
import AlertDialog from './AlertDialog.vue';
import LoadingOverlay from './LoadingOverlay.vue';
import { switchService } from '../utils/apiService.js';

const props = defineProps({
  switchId: { type: [Number, String], required: true }
});
const emit = defineEmits(['released', 'close']);

const choosing = ref(true);
const releasing = ref(false);
const outcomeMessage = ref('');

const release = async (withCleanup) => {
  choosing.value = false;
  releasing.value = true;
  const result = await switchService.release(Number(props.switchId), withCleanup);
  releasing.value = false;
  if (result.success) {
    emit('released');
    // The detail names any Cleanup or banner failure
    outcomeMessage.value = result.data?.detail || 'Switch released successfully!';
  } else {
    outcomeMessage.value = `Failed to release switch: ${result.message}`;
  }
};
</script>
