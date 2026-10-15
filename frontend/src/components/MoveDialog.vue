<template>
  <!-- Moving a reserved Switch to another of my Projects: no Release, no Cleanup. Its Links must
       be disconnected first, since Links never cross Projects; BLab says so if not. -->
  <UiConfirm
    :title="`Move ${label} to another Project`"
    confirm-label="Move"
    :danger="false"
    :pending="moving"
    :disabled="!choice"
    @confirm="move"
    @close="close"
  >
    <p v-if="!loaded" class="flex items-center gap-2 text-gray-500"><UiSpinner size="sm" /> Loading your Projects...</p>
    <p v-else-if="!targets.length" class="text-gray-600" data-testid="no-target">
      You have no other active Project. Create one on the Projects page, or join one through its Invite link.
    </p>
    <template v-else>
      <label for="move-target" class="mb-1 block text-sm font-medium text-gray-700">Move into</label>
      <select id="move-target" v-model="choice" class="input w-full">
        <option v-for="p in targets" :key="p.id" :value="String(p.id)">{{ p.name }}</option>
      </select>
      <p class="mt-3 text-sm text-gray-600">
        It keeps its holder and end date. The members of the new Project get a Switch account on it; those who aren't in it lose theirs.
      </p>
    </template>
  </UiConfirm>
</template>

<script setup>
// Emits "moved" with the Project it went into once moved, "close" when done
import { computed, onMounted, ref } from 'vue';
import UiConfirm from './ui/UiConfirm.vue';
import UiSpinner from './ui/UiSpinner.vue';
import { projectService } from '../utils/apiService.js';
import { loadProjects } from '../utils/projects.js';
import { toast } from '../composables/toast.js';

const props = defineProps({
  switchId: { type: Number, required: true },
  label: { type: String, default: 'this Switch' },
  fromProject: { type: Number, default: null },  // the Project it is in now, left out of the choice
});
const emit = defineEmits(['moved', 'close']);

const loaded = ref(false);
const active = ref([]);
const choice = ref('');
const moving = ref(false);
const targets = computed(() => active.value.filter(p => p.id !== props.fromProject));

onMounted(async () => {
  const answer = await loadProjects();
  active.value = answer.active;
  choice.value = targets.value.length ? String(targets.value[0].id) : '';
  loaded.value = true;
});

const close = () => {
  if (!moving.value) emit('close');
};

const move = async () => {
  moving.value = true;
  const result = await projectService.moveSwitch(Number(choice.value), props.switchId);
  moving.value = false;
  if (!result.success) {
    toast.error(`Couldn't move ${props.label}. ${result.message}`);
    return;
  }
  toast.success(result.data.detail);
  emit('moved', result.data.project);
  emit('close');
};
</script>
