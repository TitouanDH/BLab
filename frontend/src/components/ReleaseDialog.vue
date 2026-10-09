<template>
  <!-- Release confirmation: every Release Cleans up, so it only warns about cables left plugged -->
  <UiConfirm
    title="Release this Switch?"
    confirm-label="Release"
    :pending="releasing"
    :disabled="checking"
    @confirm="release"
    @close="close"
  >
    <p class="mb-4 text-gray-600">
      BLab disconnects its Links, then Cleans it up: it restores the init config, reloads the Switch and Inspects it.
    </p>
    <p v-if="checking" class="flex items-center gap-2 text-gray-500">
      <UiSpinner size="sm" /> Checking for cables left plugged...
    </p>
    <div v-else-if="unwantedCables && unwantedCables.length" class="rounded-md border border-warning-300 bg-warning-50 p-3 text-warning-800">
      <p class="font-medium">Cables still plugged: {{ unwantedCables.join(', ') }}</p>
      <p class="mt-1">Unplug them, or the Switch will be Quarantined in the holder's name after its Cleanup.</p>
    </div>
    <p v-else-if="unwantedCables === null" class="text-gray-500">
      BLab couldn't read the Switch's ports. Make sure no cable is left plugged, or the Switch will be Quarantined.
    </p>
  </UiConfirm>
</template>

<script setup>
// Releasing a Switch (see CONTEXT.md): shows the cables that would count as Unwanted cables,
// releases it, and reports the outcome in a toast. Emits "released" once the Reservation
// has ended, "close" when done.
import { onMounted, ref } from 'vue';
import UiConfirm from './ui/UiConfirm.vue';
import UiSpinner from './ui/UiSpinner.vue';
import { switchService } from '../utils/apiService.js';
import { toast } from '../composables/toast.js';

const props = defineProps({
  switchId: { type: [Number, String], required: true }
});
const emit = defineEmits(['released', 'close']);

const checking = ref(true);
const unwantedCables = ref([]);  // null when BLab couldn't read the ports
const releasing = ref(false);

onMounted(async () => {
  const result = await switchService.releaseCheck(Number(props.switchId));
  unwantedCables.value = result.success ? result.data.unwanted_cables : null;
  checking.value = false;
});

// Closing while the Release runs would hide its outcome: wait for it
const close = () => {
  if (!releasing.value) emit('close');
};

const release = async () => {
  releasing.value = true;
  const result = await switchService.release(Number(props.switchId));
  releasing.value = false;
  if (result.success) {
    toast.success(result.data?.detail || 'Released.');
    emit('released');
  } else {
    toast.error(`Couldn't Release this Switch. ${result.message}`);
  }
  emit('close');
};
</script>
