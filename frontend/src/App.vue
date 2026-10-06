<template>
  <div id="app">
    <div
      v-if="stage === 'preprod'"
      class="bg-amber-400 text-black text-center text-sm font-semibold py-1"
    >
      PRE-PROD ({{ commit }}): same database and real switches as production
    </div>
    <router-view />
  </div>
</template>

<script>
export default {
  name: 'App',
  data() {
    return {
      stage: import.meta.env.VITE_BLAB_STAGE,
      commit: (import.meta.env.VITE_GIT_COMMIT || 'unknown').slice(0, 7),
    }
  },
}
</script>
