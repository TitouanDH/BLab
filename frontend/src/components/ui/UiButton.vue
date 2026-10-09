<template>
  <!-- The one button. primary: the main action of a view; secondary: everything else;
       danger: destructive actions only (Release, disconnect). `pending` shows a Spinner and
       disables it while a long operation runs. `to` makes it a router link. -->
  <component
    :is="to ? 'router-link' : 'button'"
    :to="to"
    :type="to ? undefined : type"
    :disabled="to ? undefined : (disabled || pending)"
    :aria-busy="pending || undefined"
    :class="[
      'inline-flex items-center justify-center gap-2 rounded-md font-semibold transition-colors',
      'focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2',
      'disabled:cursor-not-allowed disabled:opacity-50',
      sizes[size],
      variants[variant],
    ]"
  >
    <UiSpinner v-if="pending" size="sm" :class="variant === 'secondary' ? 'text-primary-700' : 'text-white'" />
    <slot />
  </component>
</template>

<script setup>
import UiSpinner from './UiSpinner.vue';

defineProps({
  variant: { type: String, default: 'secondary', validator: v => ['primary', 'secondary', 'danger'].includes(v) },
  size: { type: String, default: 'md', validator: v => ['sm', 'md'].includes(v) },
  type: { type: String, default: 'button' },
  disabled: Boolean,
  pending: Boolean,
  to: { type: [String, Object], default: null },
});

const sizes = {
  sm: 'px-2.5 py-1 text-xs',
  md: 'px-4 py-2 text-sm',
};

const variants = {
  primary: 'bg-primary-700 text-white shadow-sm hover:bg-primary-800 focus-visible:ring-primary-600',
  secondary: 'bg-white text-gray-700 ring-1 ring-inset ring-gray-300 hover:bg-gray-50 focus-visible:ring-primary-600',
  danger: 'bg-danger-600 text-white shadow-sm hover:bg-danger-700 focus-visible:ring-danger-600',
};
</script>
