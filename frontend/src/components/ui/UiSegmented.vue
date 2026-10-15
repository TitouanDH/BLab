<template>
  <!-- One choice among a few, as joined toggle buttons (aria-pressed). options: [{ key, label }] -->
  <div class="inline-flex rounded-md shadow-sm" role="group" :aria-label="label">
    <button
      v-for="(o, i) in options"
      :key="o.key"
      type="button"
      :aria-pressed="modelValue === o.key"
      :class="[
        'px-3 py-1.5 text-sm font-medium ring-1 ring-inset ring-gray-300 focus:z-10 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-600',
        i === 0 ? 'rounded-l-md' : '-ml-px',
        i === options.length - 1 ? 'rounded-r-md' : '',
        modelValue === o.key ? 'bg-primary-700 text-white ring-primary-700' : 'bg-white text-gray-700 hover:bg-gray-50',
      ]"
      @click="$emit('update:modelValue', o.key)"
    >
      {{ o.label }}
    </button>
  </div>
</template>

<script setup>
defineProps({
  modelValue: { type: String, required: true },
  options: { type: Array, required: true },
  label: { type: String, required: true },
});
defineEmits(['update:modelValue']);
</script>
