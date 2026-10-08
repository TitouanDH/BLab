<template>
  <!-- Release confirmation: every Release Cleans up, so it only warns about cables left plugged -->
  <div v-if="confirming" class="fixed inset-0 bg-black bg-opacity-40 flex items-center justify-center z-50">
    <div class="bg-white rounded-lg shadow-lg p-6 w-full max-w-md relative">
      <button @click="$emit('close')" class="absolute top-2 right-2 text-gray-500 hover:text-gray-700 text-xl">&times;</button>

      <h3 class="text-lg font-bold mb-2">Release this Switch?</h3>
      <p class="text-sm text-gray-600 mb-4">
        BLab disconnects its Links, then Cleans it up: it restores the init config, reloads the Switch and Inspects it.
      </p>

      <p v-if="checking" class="text-sm text-gray-500 mb-4">Checking for cables left plugged...</p>
      <div v-else-if="unwantedCables && unwantedCables.length" class="mb-4 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
        <p class="font-medium">Cables still plugged: {{ unwantedCables.join(', ') }}</p>
        <p class="mt-1">Unplug them, or the Switch will be Quarantined in the holder's name after its Cleanup.</p>
      </div>
      <p v-else-if="unwantedCables === null" class="mb-4 text-sm text-gray-500">
        BLab couldn't read the Switch's ports. Make sure no cable is left plugged, or the Switch will be Quarantined.
      </p>

      <div class="flex justify-end gap-3">
        <button
          @click="$emit('close')"
          class="px-4 py-2 text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors"
        >
          Cancel
        </button>
        <button
          @click="release"
          :disabled="checking"
          class="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors disabled:opacity-50"
        >
          Release
        </button>
      </div>
    </div>
  </div>
  <LoadingOverlay v-if="releasing" />
  <AlertDialog v-if="outcomeMessage" :message="outcomeMessage" @close="$emit('close')" />
</template>

<script setup>
// Releasing a Switch (see CONTEXT.md): shows the cables that would count as Unwanted cables,
// releases it, and shows the outcome. Emits "released" once the Reservation has ended,
// "close" when done.
import { onMounted, ref } from 'vue';
import AlertDialog from './AlertDialog.vue';
import LoadingOverlay from './LoadingOverlay.vue';
import { switchService } from '../utils/apiService.js';

const props = defineProps({
  switchId: { type: [Number, String], required: true }
});
const emit = defineEmits(['released', 'close']);

const confirming = ref(true);
const checking = ref(true);
const unwantedCables = ref([]);  // null when BLab couldn't read the ports
const releasing = ref(false);
const outcomeMessage = ref('');

onMounted(async () => {
  const result = await switchService.releaseCheck(Number(props.switchId));
  unwantedCables.value = result.success ? result.data.unwanted_cables : null;
  checking.value = false;
});

const release = async () => {
  confirming.value = false;
  releasing.value = true;
  const result = await switchService.release(Number(props.switchId));
  releasing.value = false;
  if (result.success) {
    emit('released');
    outcomeMessage.value = result.data?.detail || 'Released.';
  } else {
    outcomeMessage.value = `Failed to release switch: ${result.message}`;
  }
};
</script>
