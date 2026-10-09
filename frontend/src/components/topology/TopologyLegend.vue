<template>
  <!-- What each state on the canvas means, drawn from the same tables as the canvas (model.js) -->
  <section aria-label="Legend" class="text-xs text-gray-700">
    <h2 class="font-semibold text-gray-900">Legend</h2>
    <div class="mt-2 space-y-2">
      <ul class="space-y-1.5">
        <li v-for="(s, state) in SWITCH_STATES" :key="state" class="flex items-center gap-2" :data-legend="`switch-${state}`">
          <span
            class="inline-block h-4 w-7 shrink-0 rounded"
            :style="{ background: s.fill, borderColor: s.color, borderStyle: s.border, borderWidth: `${Math.min(s.width, 4)}px` }"
          ></span>
          {{ s.label }}
        </li>
      </ul>
      <ul class="space-y-1.5 border-t border-gray-100 pt-2">
        <li v-for="(s, state) in PORT_STATES" :key="state" class="flex items-center gap-2" :data-legend="`port-${state}`">
          <span class="flex w-7 shrink-0 justify-center">
            <span class="inline-block h-3 w-3 rounded-sm border-2" :style="{ background: s.fill, borderColor: s.color }"></span>
          </span>
          {{ s.label }}
        </li>
      </ul>
      <ul class="space-y-1.5 border-t border-gray-100 pt-2">
        <li v-for="(s, state) in LINK_STATES" :key="state" class="flex items-center gap-2" :data-legend="`link-${state}`">
          <svg class="h-3 w-7 shrink-0" viewBox="0 0 28 12" aria-hidden="true">
            <line
              x1="1" y1="6" x2="27" y2="6"
              :stroke="s.color"
              :stroke-width="s.width"
              :stroke-dasharray="s.dash ? s.dash.join(' ') : undefined"
              stroke-linecap="butt"
            />
          </svg>
          {{ s.label }}
        </li>
      </ul>
    </div>
  </section>
</template>

<script setup>
import { LINK_STATES, PORT_STATES, SWITCH_STATES } from '../../topology/model.js';
</script>
