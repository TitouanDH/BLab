<template>
  <!-- Contribute (CONTEXT.md, Contribution): a cable (#58) or a Switch (#59), guided, with no admin approval.
       It opens on a card for each (#84); a card opens its flow, at its own address, with a way back. -->
  <div class="container mx-auto max-w-2xl px-4 py-8">
    <template v-if="current">
      <router-link to="/contribute" class="text-sm text-primary-700 hover:underline">&larr; Contribute</router-link>
      <h1 class="mt-1 text-2xl font-bold text-gray-900">{{ current.title }}</h1>
      <component :is="current.component" class="mt-4" />
    </template>

    <template v-else>
      <h1 class="text-2xl font-bold text-gray-900">Contribute</h1>
      <p class="mt-1 text-sm text-gray-600">Add equipment to the lab. BLab guides you and checks each step: no admin needs to approve it.</p>
      <div class="mt-6 grid gap-4 sm:grid-cols-2">
        <router-link
          v-for="card in CARDS" :key="card.key" :to="{ query: { what: card.key } }" :data-testid="`contribute-${card.key}`"
          :aria-labelledby="`contribute-${card.key}-title`" :aria-describedby="`contribute-${card.key}-does`"
          class="group flex flex-col rounded-lg bg-white p-6 shadow-sm ring-1 ring-gray-200 transition hover:shadow-md hover:ring-primary-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
        >
          <div class="flex items-start justify-between gap-2">
            <component :is="card.icon" class="h-8 w-8 text-primary-700" aria-hidden="true" />
            <UiBadge v-if="card.key === 'switch' && inProgress" tone="primary" data-testid="in-progress">{{ inProgress }} in progress</UiBadge>
          </div>
          <h2 :id="`contribute-${card.key}-title`" class="mt-3 text-lg font-semibold text-gray-900 group-hover:text-primary-700">{{ card.title }}</h2>
          <p :id="`contribute-${card.key}-does`" class="mt-1 text-sm text-gray-600">{{ card.does }}</p>
          <p class="mt-3 text-sm text-gray-500"><span class="font-medium text-gray-700">You need:</span> {{ card.need }}</p>
        </router-link>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { LinkIcon, ServerStackIcon } from '@heroicons/vue/24/outline';
import UiBadge from '../components/ui/UiBadge.vue';
import ContributeCable from '../components/contribute/ContributeCable.vue';
import ContributeSwitch from '../components/contribute/ContributeSwitch.vue';
import { switchContributionService } from '../utils/apiService.js';

const CARDS = [
  {
    key: 'cable', title: 'Contribute a cable', icon: LinkIcon, component: ContributeCable,
    does: 'Give a Switch one more Port, cabled to a Backbone.',
    need: 'a cable, and optics if the ports take them.',
  },
  {
    key: 'switch', title: 'Contribute a Switch', icon: ServerStackIcon, component: ContributeSwitch,
    does: 'Add a new Switch to the lab.',
    need: 'the Switch on the management network, and its console.',
  },
];

const route = useRoute();
// ?what=cable or ?what=switch opens a flow. ?switch=<id> opens the cable flow for that Switch: the
// links to add a cable give it, and the cable flow keeps it in the address once a Switch is picked.
const current = computed(() => CARDS.find(card => card.key === (route.query.what || (route.query.switch ? 'cable' : null))));

// My Switch Contributions not yet in the lab, for the card (they are in its flow), each time the cards show
const inProgress = ref(0);
watch(current, async (flow) => {
  if (flow) return;
  const result = await switchContributionService.list();
  if (result.success) inProgress.value = result.data.contributions.filter(c => c.state !== 'done' && !c.expired).length;
}, { immediate: true });
</script>
