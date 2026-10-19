<template>
  <!-- A small pill over the canvas; opened, it says what each state on the canvas means. Only
       the states drawn right now are listed, from the same tables as the canvas (model.js). -->
  <div class="text-xs text-gray-700">
    <section
      v-if="open"
      id="topology-legend"
      aria-label="Legend"
      class="mb-2 w-64 rounded-lg border border-gray-200 bg-white/95 p-3 shadow-md"
    >
      <ul v-if="switchStates.length" class="space-y-1.5">
        <li v-for="state in switchStates" :key="state" class="flex items-center gap-2" :data-legend="`switch-${state}`">
          <img :src="swatchImage(SWITCH_STATES[state])" alt="" class="h-4 w-7 shrink-0" />
          <span><span class="font-semibold" :style="{ color: SWITCH_STATES[state].chipInk }">{{ SWITCH_STATES[state].chip }}</span>: {{ SWITCH_STATES[state].label }}</span>
        </li>
      </ul>
      <ul v-if="linkStates.length" :class="['space-y-1.5', switchStates.length ? 'mt-2 border-t border-gray-100 pt-2' : '']">
        <li v-for="state in linkStates" :key="state" class="flex items-center gap-2" :data-legend="`link-${state}`">
          <svg class="h-3 w-7 shrink-0" viewBox="0 0 28 12" aria-hidden="true">
            <line
              x1="1" y1="6" x2="27" y2="6"
              :stroke="LINK_STATES[state].color"
              :stroke-width="LINK_STATES[state].width"
              :stroke-dasharray="LINK_STATES[state].dash ? LINK_STATES[state].dash.join(' ') : undefined"
              stroke-linecap="butt"
            />
          </svg>
          {{ LINK_STATES[state].label }}
        </li>
      </ul>
    </section>
    <button
      type="button"
      class="rounded-full border border-gray-200 bg-white/95 px-3 py-1 font-semibold text-gray-700 shadow-sm hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-600"
      :aria-expanded="open"
      aria-controls="topology-legend"
      @click="open = !open"
    >
      Legend
    </button>
  </div>
</template>

<script setup>
import { ref } from 'vue';
import { swatchImage } from '../../topology/cards.js';
import { LINK_STATES, SWITCH_STATES } from '../../topology/model.js';

// The states drawn, in the tables' order
defineProps({
  switchStates: { type: Array, default: () => [] },
  linkStates: { type: Array, default: () => [] },
});

const open = ref(false);
</script>
