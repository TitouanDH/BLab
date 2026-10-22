<template>
  <!-- Contribute a cable (CONTEXT.md, Contribution; #58): pick a Switch, BLab proposes the cable,
       the user plugs it and presses Plugged, BLab checks it and records the Port. No admin approval. -->
  <div>
    <p class="text-sm text-gray-600">
      Give a Switch one more Port: a cable between the Switch and a Backbone. BLab tells you where to plug it,
      checks it once you have, and takes it in. No admin needs to approve it.
    </p>

    <div v-if="loading" class="flex justify-center py-16 text-primary-700"><UiSpinner size="lg" /></div>
    <p v-else-if="loadProblem" class="mt-6 text-sm text-warning-800">{{ loadProblem }}</p>

    <section v-else class="mt-6 rounded-lg bg-white p-6 shadow-sm ring-1 ring-gray-200" aria-label="The cable">
      <label for="contribute-switch" class="block text-sm font-medium text-gray-700">Switch</label>
      <select id="contribute-switch" v-model="switchId" class="input mt-1 w-full" @change="propose()">
        <option :value="null" disabled>Pick a Switch</option>
        <option v-for="sw in switches" :key="sw.id" :value="sw.id">{{ switchLabel(sw) }} ({{ plural(sw.ports, 'Port', 'Ports') }})</option>
      </select>
      <p v-if="!switches.length" class="mt-2 text-sm text-gray-600">
        No Switch you may add a cable to: it must be free, or one you may work on.
      </p>

      <div v-if="proposing" class="flex justify-center py-8 text-primary-700"><UiSpinner /></div>
      <template v-else-if="proposal">
        <div v-if="proposal.backbones.length > 1 || !proposal.backbone" class="mt-4">
          <label for="contribute-backbone" class="block text-sm font-medium text-gray-700">Backbone</label>
          <select id="contribute-backbone" v-model="backbone" class="input mt-1 w-full" @change="propose(backbone)">
            <option :value="null" disabled>Pick a Backbone</option>
            <option v-for="bb in proposal.backbones" :key="bb" :value="bb">{{ bb }}</option>
          </select>
        </div>

        <p v-if="proposal.problem" class="mt-4 text-sm text-warning-800" data-testid="proposal-problem">{{ proposal.problem }}</p>
        <div v-else class="mt-4 rounded-md border border-primary-200 bg-primary-50 p-4" data-testid="instruction">
          <p class="text-sm text-gray-700">Plug a cable between</p>
          <p class="mt-1 text-lg font-semibold text-gray-900">
            {{ chosenLabel }} <span class="font-mono">{{ switchPort }}</span>
            <span aria-hidden="true"> &rarr; </span><span class="sr-only"> and </span>
            Backbone {{ proposal.backbone }} <span class="font-mono">{{ backbonePort }}</span>
          </p>
          <p class="mt-1 text-xs text-gray-600" data-testid="speeds">
            {{ switchChoice?.speeds }} <span aria-hidden="true">&harr;</span><span class="sr-only"> and </span> {{ backboneChoice?.speeds }}
          </p>

          <!-- #82: any other pair of free ports that share a speed, the proposal staying the default -->
          <details class="mt-3 text-sm" data-testid="other-ports">
            <summary class="cursor-pointer text-primary-700 hover:underline">Plug into other ports</summary>
            <div class="mt-2 grid gap-3 sm:grid-cols-2">
              <div>
                <label for="contribute-switch-port" class="block text-sm font-medium text-gray-700">Switch port</label>
                <select id="contribute-switch-port" v-model="switchPort" class="input mt-1 w-full" @change="pickSwitchPort">
                  <option v-for="choice in proposal.choices || []" :key="choice.port" :value="choice.port">{{ choice.port }} ({{ choice.speeds }})</option>
                </select>
              </div>
              <div>
                <label for="contribute-backbone-port" class="block text-sm font-medium text-gray-700">Port on Backbone {{ proposal.backbone }}</label>
                <select id="contribute-backbone-port" v-model="backbonePort" class="input mt-1 w-full">
                  <option v-for="bb in switchChoice?.backbone_ports || []" :key="bb.port" :value="bb.port">{{ bb.port }} ({{ bb.speeds }})</option>
                </select>
              </div>
            </div>
            <p class="mt-2 text-xs text-gray-600">
              Only free ports that share a speed are listed. Copper goes with copper and optics with optics, unless you
              have a copper optic.
            </p>
          </details>
          <p class="mt-2 text-sm text-gray-600">
            Then press Plugged. BLab enables the Backbone port for a few seconds to check that this cable joins exactly
            those two ports, then records the Port.
          </p>
          <div class="mt-4 flex gap-2">
            <UiButton variant="primary" :pending="checking" @click="plugged">Plugged</UiButton>
            <UiButton :disabled="checking" @click="propose(backbone)">Propose again</UiButton>
          </div>
        </div>
      </template>

      <!-- Why the proposal or the check failed: the user fixes it and tries again -->
      <div v-if="failure" role="alert" class="mt-4 rounded-md border border-warning-300 bg-warning-50 p-3 text-sm text-warning-800" data-testid="check-failure">
        <p class="font-medium">{{ proposal ? 'Not taken in yet' : 'No cable to propose' }}</p>
        <p class="mt-1">{{ failure }}</p>
      </div>

      <div v-if="done" role="status" class="mt-4 rounded-md border border-success-300 bg-success-50 p-3 text-sm text-success-800" data-testid="check-done">
        <p>{{ done }}</p>
        <p class="mt-1">More cables make the Switch more useful: you may add another one.</p>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import UiButton from '../ui/UiButton.vue';
import UiSpinner from '../ui/UiSpinner.vue';
import { contributeService } from '../../utils/apiService.js';
import { switchLabel } from '../../utils/switchLabel.js';
import { plural } from '../../utils/plural.js';

const route = useRoute();
const router = useRouter();
const loading = ref(true);
const loadProblem = ref('');
const switches = ref([]);
const switchId = ref(null);
const backbone = ref(null);
const proposal = ref(null);
const proposing = ref(false);
const checking = ref(false);
const failure = ref('');
const done = ref('');

const switchPort = ref(null);
const backbonePort = ref(null);

const chosen = computed(() => switches.value.find(sw => sw.id === switchId.value));
const chosenLabel = computed(() => (chosen.value ? switchLabel(chosen.value) : ''));
const switchChoice = computed(() => (proposal.value?.choices || []).find(choice => choice.port === switchPort.value));
const backboneChoice = computed(() => switchChoice.value?.backbone_ports.find(bb => bb.port === backbonePort.value));

// Another Switch port: its best Backbone port (the same medium first, as the server orders them)
const pickSwitchPort = () => {
  backbonePort.value = switchChoice.value?.backbone_ports[0]?.port ?? null;
};

// The cable to plug, for the chosen Switch (and Backbone, once picked)
const propose = async (bb = null) => {
  if (switchId.value === null) return;
  router.replace({ query: { switch: switchId.value } });
  proposing.value = true;
  failure.value = '';
  done.value = '';
  const result = await contributeService.propose(switchId.value, bb);
  proposing.value = false;
  if (!result.success) {
    proposal.value = null;
    failure.value = result.message;
    return;
  }
  proposal.value = result.data.proposal;
  backbone.value = proposal.value.backbone;
  switchPort.value = proposal.value.switch_port;
  backbonePort.value = proposal.value.backbone_port;
};

const plugged = async () => {
  checking.value = true;
  failure.value = '';
  const result = await contributeService.plugged({
    switch: switchId.value, switch_port: switchPort.value, backbone: proposal.value.backbone,
    backbone_port: backbonePort.value,
  });
  checking.value = false;
  if (!result.success) {
    failure.value = result.message;
    return;
  }
  if (chosen.value) chosen.value.ports += 1;
  proposal.value = null;
  done.value = result.data.detail;
};

onMounted(async () => {
  const result = await contributeService.switches();
  loading.value = false;
  if (!result.success) {
    loadProblem.value = `Couldn't load the Switches. ${result.message}`;
    return;
  }
  switches.value = result.data.switches;
  const asked = Number(route.query.switch);
  if (switches.value.some(sw => sw.id === asked)) {
    switchId.value = asked;
    propose();
  }
});
</script>
