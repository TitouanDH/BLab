<template>
  <!-- How many ports a Switch has cabled to a backbone (how many Links it can carry): pips and
       the count, or the count alone with `bare` -->
  <span
    class="inline-flex items-center gap-0.5"
    :title="count === null ? 'Cabled ports unknown' : `${plural(count, 'cabled port', 'cabled ports')}: how many Links it can carry`"
  >
    <template v-if="!bare">
      <span v-for="i in PIPS" :key="i" :class="['h-2.5 w-1.5 rounded-sm', i <= (count ?? 0) ? 'bg-primary-600' : 'bg-gray-200']" />
    </template>
    <span :class="['min-w-[1ch] tabular-nums', bare ? (count ? 'font-semibold text-gray-900' : 'text-gray-400') : 'ml-1.5 text-xs text-gray-700']" data-testid="cabled-ports">
      {{ count ?? '-' }}
    </span>
  </span>
</template>

<script setup>
import { plural } from '../../utils/plural.js';

defineProps({
  count: { type: Number, default: null },
  bare: Boolean,  // no pips: the cards' corner
});
const PIPS = 8;  // a 1U Switch rarely has more cabled; more fills them all
</script>
