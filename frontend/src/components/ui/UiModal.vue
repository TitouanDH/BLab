<template>
  <!-- The one modal. Only for confirming a destructive action, Connect on the Topology canvas
       (#28: confirmed with both port names), or a short form (e.g. the Reserve dialog):
       results go to toasts (composables/toast.js), never to a modal.
       Closes on Esc, on the backdrop and on the x; the parent decides with v-if. -->
  <Teleport to="body">
    <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" @mousedown.self="$emit('close')">
      <div
        role="dialog"
        aria-modal="true"
        :aria-labelledby="titleId"
        :class="['relative w-full rounded-lg bg-white p-6 shadow-xl', widths[width]]"
      >
        <button
          type="button"
          class="absolute right-3 top-3 rounded p-1 text-gray-400 hover:text-gray-600"
          aria-label="Close"
          @click="$emit('close')"
        >
          <XMarkIcon class="h-5 w-5" />
        </button>
        <h3 :id="titleId" class="mb-3 pr-6 text-lg font-semibold text-gray-900">{{ title }}</h3>
        <div class="text-sm text-gray-700">
          <slot />
        </div>
        <div v-if="$slots.actions" class="mt-6 flex justify-end gap-3">
          <slot name="actions" />
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { onBeforeUnmount, onMounted } from 'vue';
import { XMarkIcon } from '@heroicons/vue/24/outline';

defineProps({
  title: { type: String, required: true },
  width: { type: String, default: 'md', validator: v => ['md', 'lg'].includes(v) },
});
const emit = defineEmits(['close']);

const titleId = `modal-title-${Math.random().toString(36).slice(2)}`;
const widths = { md: 'max-w-md', lg: 'max-w-2xl' };

const onKey = (event) => { if (event.key === 'Escape') emit('close'); };
onMounted(() => document.addEventListener('keydown', onKey));
onBeforeUnmount(() => document.removeEventListener('keydown', onKey));
</script>
