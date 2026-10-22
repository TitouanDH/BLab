<template>
  <!-- Suggests Contribute a cable (#58) for one Switch: when it has no free Port ('out-of-ports'),
       or when its Quarantine is for an Unwanted cable that may stay on a Backbone ('unwanted-cable') -->
  <p data-testid="contribute-nudge">
    <template v-if="why === 'out-of-ports'">Out of ports?</template>
    <template v-else>Want that cable to stay? Plug it into a Backbone instead:</template>
    <router-link :to="{ path: '/contribute', query: { switch: switchId } }" class="text-primary-700 underline">Contribute a cable</router-link>
  </p>
</template>

<script setup>
defineProps({
  switchId: { type: Number, required: true },
  why: { type: String, required: true, validator: v => ['out-of-ports', 'unwanted-cable'].includes(v) },
});
</script>
