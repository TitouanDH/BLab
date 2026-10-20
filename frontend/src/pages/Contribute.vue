<template>
  <!-- Contribute (CONTEXT.md, Contribution): a cable (#58) or a Switch (#59), guided, with no admin approval -->
  <div class="container mx-auto max-w-2xl px-4 py-8">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <h1 class="text-2xl font-bold text-gray-900">Contribute</h1>
      <UiSegmented :model-value="what" label="Contribute" :options="WHAT" @update:model-value="pick" />
    </div>
    <ContributeSwitch v-if="what === 'switch'" class="mt-4" />
    <ContributeCable v-else class="mt-4" />
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import UiSegmented from '../components/ui/UiSegmented.vue';
import ContributeCable from '../components/contribute/ContributeCable.vue';
import ContributeSwitch from '../components/contribute/ContributeSwitch.vue';

const WHAT = [{ key: 'cable', label: 'A cable' }, { key: 'switch', label: 'A Switch' }];

const route = useRoute();
const router = useRouter();
const what = computed(() => (route.query.what === 'switch' ? 'switch' : 'cable'));
const pick = (key) => router.replace({ query: key === 'switch' ? { what: 'switch' } : {} });
</script>
