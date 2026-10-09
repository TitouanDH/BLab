<template>
  <!-- A state at a glance. Tones: success and warning for status, neutral for facts,
       dark for Out of service, primary for work BLab is doing (Cleanup). Never red:
       red is for destructive actions only. -->
  <span :class="['inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold', tones[tone]]">
    <slot />
  </span>
</template>

<script setup>
defineProps({
  tone: {
    type: String,
    default: 'neutral',
    validator: v => ['neutral', 'success', 'warning', 'strong-warning', 'dark', 'primary'].includes(v),
  },
});

const tones = {
  neutral: 'bg-gray-100 text-gray-700',
  success: 'bg-success-100 text-success-800',
  warning: 'bg-warning-100 text-warning-800',
  'strong-warning': 'bg-warning-500 text-white',
  dark: 'bg-gray-700 text-white',
  primary: 'bg-primary-100 text-primary-800',
};
</script>
