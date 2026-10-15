<template>
  <!-- The Reservation page's facets: a minimum of cabled ports, and the models to show, under
       their family, each with how many of its Switches are free (whatever the filters) -->
  <div class="text-sm">
    <div class="mb-3">
      <label :for="`${idPrefix}min-ports`" class="mb-1 block text-xs font-semibold uppercase tracking-wide text-gray-500">
        At least {{ plural(minPorts, 'cabled port', 'cabled ports') }}
      </label>
      <input :id="`${idPrefix}min-ports`" v-model.number="minPorts" type="range" min="0" :max="maxPorts" class="w-full accent-primary-700" />
    </div>
    <div class="mb-1 flex items-center justify-between text-xs font-semibold uppercase tracking-wide text-gray-500">
      Models
      <button v-if="picked.length" type="button" class="normal-case text-primary-700 hover:underline" @click="picked = []">Clear</button>
    </div>
    <div class="max-h-[70vh] overflow-y-auto pr-1">
      <div v-for="family in families" :key="family.name" class="mb-1" :data-family="family.name">
        <button
          type="button"
          class="flex w-full items-center justify-between rounded px-1 py-0.5 font-semibold text-gray-900 hover:bg-gray-50"
          :aria-pressed="family.models.every(m => picked.includes(m.name))"
          :title="`Show only ${family.name} Switches, or all again`"
          @click="pickFamily(family)"
        >
          {{ family.name }}
          <span class="text-xs font-normal" :class="family.free ? 'text-success-700' : 'text-gray-400'" data-testid="free-count">
            {{ family.free }}/{{ family.total }}
          </span>
        </button>
        <label
          v-for="m in family.models" :key="m.name"
          class="flex cursor-pointer items-center justify-between rounded py-0.5 pl-3 pr-1 text-gray-700 hover:bg-gray-50"
          :data-model="m.name"
        >
          <span class="flex items-center gap-2">
            <input
              type="checkbox" class="h-3.5 w-3.5 rounded border-gray-300 text-primary-700 focus:ring-primary-600"
              :checked="picked.includes(m.name)" :aria-label="m.name" @change="toggle(m.name)"
            />
            {{ m.name.slice(family.name.length) || m.name }}
          </span>
          <span class="text-xs" :class="m.free ? 'text-success-700' : 'text-gray-400'" data-testid="free-count">{{ m.free }}/{{ m.total }}</span>
        </label>
      </div>
    </div>
  </div>
</template>

<script setup>
import { plural } from '../../utils/plural.js';

defineProps({
  families: { type: Array, required: true },  // groupByFamily() of every Switch, whatever the filters
  maxPorts: { type: Number, required: true },
  idPrefix: { type: String, default: '' },  // the facets show twice: in the sidebar and, on a phone, above the list
});
const picked = defineModel('picked', { type: Array, required: true });  // model names; none picked shows every model
const minPorts = defineModel('minPorts', { type: Number, required: true });

const toggle = (model) => {
  picked.value = picked.value.includes(model) ? picked.value.filter(m => m !== model) : [...picked.value, model];
};

// All of a family's models, or none of them if all are picked already
const pickFamily = (family) => {
  const names = family.models.map(m => m.name);
  picked.value = names.every(m => picked.value.includes(m))
    ? picked.value.filter(m => !names.includes(m))
    : [...new Set([...picked.value, ...names])];
};
</script>
