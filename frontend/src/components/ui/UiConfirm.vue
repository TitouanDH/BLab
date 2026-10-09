<template>
  <!-- Confirmation before an action: Cancel, and the action, in red when destructive (the default).
       Connect on the Topology canvas is confirmed too, naming both ports, with danger off. -->
  <UiModal :title="title" @close="$emit('close')">
    <slot>{{ message }}</slot>
    <template #actions>
      <UiButton @click="$emit('close')">Cancel</UiButton>
      <UiButton :variant="danger ? 'danger' : 'primary'" :pending="pending" :disabled="disabled" @click="$emit('confirm')">
        {{ confirmLabel }}
      </UiButton>
    </template>
  </UiModal>
</template>

<script setup>
import UiButton from './UiButton.vue';
import UiModal from './UiModal.vue';

defineProps({
  title: { type: String, required: true },
  message: { type: String, default: '' },
  confirmLabel: { type: String, required: true },
  danger: { type: Boolean, default: true },
  pending: Boolean,
  disabled: Boolean,
});
defineEmits(['confirm', 'close']);
</script>
