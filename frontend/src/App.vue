<template>
  <!-- The one layout shell: pre-prod banner, navbar, the page, toasts. Pages render only
       their content. A route with meta.fill (the Topology canvas) gets exactly the height
       left under the navbar instead of a scrolling page. -->
  <div :class="['flex flex-col bg-gray-50', $route.meta.fill ? 'h-screen overflow-hidden' : 'min-h-screen']">
    <div
      v-if="stage === 'preprod'"
      class="bg-warning-400 py-1 text-center text-sm font-semibold text-black"
    >
      PRE-PROD ({{ commit }}): same database and real switches as production
    </div>
    <Navbar />
    <main class="flex min-h-0 flex-1 flex-col">
      <router-view />
    </main>
    <UiToasts />
  </div>
</template>

<script setup>
import Navbar from './components/Navbar.vue';
import UiToasts from './components/ui/UiToasts.vue';

const stage = import.meta.env.VITE_BLAB_STAGE;
const commit = (import.meta.env.VITE_GIT_COMMIT || 'unknown').slice(0, 7);
</script>
