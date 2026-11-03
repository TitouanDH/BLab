<template>
  <!-- Pick a Switch by typing part of its model or IP (#93): a combobox, since a native select of
       every Switch in the lab is too long to scan. Arrows move, Enter picks, Escape closes. -->
  <Combobox v-slot="{ open }" :model-value="model" as="div" class="relative" @update:model-value="pick">
    <ComboboxLabel class="block text-sm font-medium text-gray-700">{{ label }}</ComboboxLabel>
    <div class="relative mt-1">
      <ComboboxInput
        :id="id"
        class="input w-full pr-8"
        autocomplete="off"
        spellcheck="false"
        :placeholder="placeholder"
        :display-value="(swId) => labelOf(swId)"
        @change="query = $event.target.value"
        @focus="$event.target.select()"
        @click="open || button?.$el.click()"
      />
      <ComboboxButton ref="button" class="absolute inset-y-0 right-0 flex items-center px-2 text-gray-400" aria-label="All Switches">
        <svg class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
          <path fill-rule="evenodd" d="M5.22 8.22a.75.75 0 0 1 1.06 0L10 11.94l3.72-3.72a.75.75 0 1 1 1.06 1.06l-4.25 4.25a.75.75 0 0 1-1.06 0L5.22 9.28a.75.75 0 0 1 0-1.06Z" clip-rule="evenodd" />
        </svg>
      </ComboboxButton>
    </div>

    <ComboboxOptions
      class="absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-md bg-white py-1 text-sm shadow-lg ring-1 ring-gray-200 focus:outline-none"
    >
      <ComboboxOption v-for="sw in matches" :key="sw.id" v-slot="{ active, selected }" :value="sw.id" as="template">
        <li :class="['flex cursor-pointer items-center gap-3 px-3 py-2', active ? 'bg-primary-50' : '']">
          <span :class="['min-w-0 flex-1 truncate text-gray-900', selected ? 'font-semibold' : '']">{{ sw.model }}</span>
          <span class="font-mono text-gray-700">{{ hasManagementIP(sw.mngt_IP) ? sw.mngt_IP : '' }}</span>
          <span class="w-16 text-right text-xs text-gray-500">{{ plural(sw.ports, 'Port', 'Ports') }}</span>
        </li>
      </ComboboxOption>
      <li v-if="!matches.length" class="px-3 py-2 text-gray-600" data-testid="no-switch-match">
        No Switch matches “{{ query }}”.
      </li>
    </ComboboxOptions>
  </Combobox>
</template>

<script setup>
import { computed, ref } from 'vue';
import { Combobox, ComboboxButton, ComboboxInput, ComboboxLabel, ComboboxOption, ComboboxOptions } from '@headlessui/vue';
import { hasManagementIP, switchLabel } from '../../utils/switchLabel.js';
import { plural } from '../../utils/plural.js';

const props = defineProps({
  id: { type: String, required: true },
  label: { type: String, default: 'Switch' },
  switches: { type: Array, required: true },  // [{ id, model, mngt_IP, ports }]
  placeholder: { type: String, default: 'Type a model or an IP' },
});
const model = defineModel({ type: Number, default: null });  // the picked Switch's id
const emit = defineEmits(['pick']);

const query = ref('');
const button = ref(null);  // clicking the box opens the list too, as the button does
const labelOf = (swId) => {
  const sw = props.switches.find(s => s.id === swId);
  return sw ? switchLabel(sw) : '';
};

// Every word typed must be in the Switch's model or IP: "6900 145.1" finds OS6900-X20 10.69.145.12
const matches = computed(() => {
  const words = query.value.toLowerCase().split(/\s+/).filter(Boolean);
  return props.switches.filter(sw => {
    const text = `${sw.model} ${sw.mngt_IP}`.toLowerCase();
    return words.every(word => text.includes(word));
  });
});

// Another Switch picked: the page proposes its cable
const pick = (swId) => {
  query.value = '';
  if (swId === model.value) return;
  model.value = swId;
  emit('pick', swId);
};
</script>
