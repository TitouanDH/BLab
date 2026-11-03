<template>
  <!-- A member's AOS install (#49): another release of the Switch's family, which BLab downloads,
       copies into working/ and reboots the Switch into. Its next Cleanup brings back the lab's
       release. While an install is under way, the dialog shows how it goes. -->
  <UiConfirm
    :title="`AOS release of ${label}`"
    confirm-label="Install and reboot"
    :pending="asking"
    :disabled="!loaded || !!refusal || !choice || underWay || choice === view?.current"
    @confirm="install"
    @close="$emit('close')"
  >
    <p v-if="!loaded" class="flex items-center gap-2 text-gray-500"><UiSpinner size="sm" /> Loading its releases...</p>
    <p v-else-if="refusal" class="text-gray-600" data-testid="aos-refusal">{{ refusal }}</p>
    <template v-else>
      <p class="mb-3 text-sm text-gray-600">
        It runs <span class="font-medium" data-testid="aos-current">{{ view.current || 'a release BLab hasn\'t read yet' }}</span><template v-if="view.lab_release">;
        the lab's is {{ view.lab_release }}</template>.
      </p>

      <div v-if="latest" class="mb-3 rounded-md p-3 text-sm" :class="installTone" data-testid="aos-install" :data-state="latest.state">
        <div class="flex items-center gap-2 font-medium">
          <UiSpinner v-if="latest.under_way" size="sm" />
          AOS {{ latest.release }}: {{ STATE_LABELS[latest.state] }}
        </div>
        <div class="mt-1">{{ latest.detail }}</div>
        <ul v-if="latest.warnings.length" class="mt-1 list-disc pl-5">
          <li v-for="w in latest.warnings" :key="w">{{ w }}</li>
        </ul>
      </div>

      <template v-if="!underWay">
        <p v-if="!view.releases.length" class="text-gray-600" data-testid="aos-no-release">
          BLab knows no AOS release for a {{ view.switch.model }}.
        </p>
        <template v-else>
          <label for="aos-release" class="mb-1 block text-sm font-medium text-gray-700">AOS release</label>
          <select id="aos-release" v-model="choice" class="input w-full">
            <option v-for="r in view.releases" :key="r" :value="r">
              {{ r }}{{ r === view.lab_release ? ' (the lab\'s)' : '' }}{{ r === view.current ? ' (running)' : '' }}
            </option>
          </select>
          <p class="mt-3 text-sm text-gray-600">
            BLab copies it into working/ and reboots the Switch, which takes 10 to 20 minutes. It boots
            working/vcboot.cfg, so <span class="font-medium">save your config first</span>: unsaved changes are lost.
            Its Links stay. When it is Released, its Cleanup brings back the lab's release.
          </p>
        </template>
      </template>
    </template>
  </UiConfirm>
</template>

<script setup>
// Emits "installing" once an install is asked for, "close" when done
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import UiConfirm from './ui/UiConfirm.vue';
import UiSpinner from './ui/UiSpinner.vue';
import { aosReleaseService } from '../utils/apiService.js';
import { toast } from '../composables/toast.js';

const props = defineProps({
  switchId: { type: Number, required: true },
  label: { type: String, default: 'this Switch' },
});
const emit = defineEmits(['installing', 'close']);

const STATE_LABELS = {
  waiting: 'waiting for BLab', installing: 'installing', reloading: 'rebooting', done: 'done', failed: 'failed',
};
const POLL_MS = 5 * 1000;

const loaded = ref(false);
const view = ref(null);  // switches/<id>/aos_release/
const refusal = ref('');  // why it can't be shown (not ours any more...)
const choice = ref('');
const asking = ref(false);
let timer = null;

const latest = computed(() => view.value?.install || null);
const underWay = computed(() => !!latest.value?.under_way);
const installTone = computed(() => ({
  done: 'bg-success-50 text-success-800',
  failed: 'bg-danger-50 text-danger-800',
}[latest.value?.state] || 'bg-primary-50 text-primary-800'));

const load = async () => {
  const result = await aosReleaseService.get(props.switchId);
  if (result.success) {
    view.value = result.data;
    if (!choice.value) {
      // Another than the one it runs, the lab's first
      const others = result.data.releases.filter(r => r !== result.data.current);
      choice.value = others.includes(result.data.lab_release) ? result.data.lab_release : (others[0] || '');
    }
  } else if (!loaded.value) {
    refusal.value = result.message;
  }
  loaded.value = true;
};

onMounted(async () => {
  await load();
  timer = setInterval(() => { if (underWay.value) load(); }, POLL_MS);
});
onBeforeUnmount(() => clearInterval(timer));

const install = async () => {
  asking.value = true;
  const result = await aosReleaseService.install(props.switchId, choice.value);
  asking.value = false;
  if (!result.success) {
    toast.error(`Couldn't install AOS ${choice.value}. ${result.message}`);
    return;
  }
  toast.success(`BLab installs AOS ${choice.value} and reboots ${props.label}.`);
  view.value = { ...view.value, install: result.data };
  emit('installing');
};
</script>
