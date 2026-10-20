<template>
  <!-- Contribute a Switch (CONTEXT.md, Contribution; #59): BLab holds a management address, gives the
       workflow with every command filled in, and once Done takes the Switch in (api/api/contribute_switch.py) -->
  <div>
    <p class="text-sm text-gray-600">
      Add a new Switch to the lab. BLab picks its management address and gives you the commands to paste at its
      console. Once it answers, BLab takes it in: it installs the lab's config, Cleans it up, reads its cables and
      puts it in service. No admin needs to approve it.
    </p>

    <div v-if="loading" class="flex justify-center py-16 text-primary-700"><UiSpinner size="lg" /></div>
    <p v-else-if="loadProblem" class="mt-6 text-sm text-warning-800">{{ loadProblem }}</p>

    <template v-else>
      <form class="mt-6 rounded-lg bg-white p-6 shadow-sm ring-1 ring-gray-200" aria-label="A new Switch" @submit.prevent="start">
        <label for="contribute-model" class="block text-sm font-medium text-gray-700">Model</label>
        <input id="contribute-model" v-model="model" list="contribute-models" class="input mt-1 w-full"
               placeholder="As show chassis names it, e.g. OS6860E-24" required>
        <datalist id="contribute-models"><option v-for="m in models" :key="m" :value="m" /></datalist>
        <label class="mt-3 flex items-center gap-2 text-sm text-gray-700">
          <input v-model="hasEmp" type="checkbox" class="rounded border-gray-300"> It has an EMP (a management port)
        </label>
        <UiButton type="submit" variant="primary" class="mt-4" :pending="starting">Get its address and commands</UiButton>
      </form>

      <section v-for="c in contributions" :key="c.id" class="mt-6 rounded-lg bg-white p-6 shadow-sm ring-1 ring-gray-200"
               :aria-label="`${c.model} ${c.mngt_IP}`" data-testid="contribution">
        <div class="flex flex-wrap items-baseline justify-between gap-2">
          <h2 class="text-lg font-semibold text-gray-900">{{ c.model }} <span class="font-mono">{{ c.mngt_IP }}</span></h2>
          <UiBadge :tone="STATES[c.state].tone">{{ STATES[c.state].label }}</UiBadge>
        </div>

        <template v-if="c.state === 'held' && c.expired">
          <p class="mt-1 text-sm text-warning-800" data-testid="contribution-expired">
            BLab held {{ c.mngt_IP }} until {{ formatDate(c.expires_at) }}: it is free again. Start a new Contribution, which
            may give another address.
          </p>
          <UiButton class="mt-4" :disabled="acting === c.id" @click="cancel(c)">Remove</UiButton>
        </template>
        <template v-else-if="c.state === 'held'">
          <p class="mt-1 text-sm text-gray-600">BLab holds {{ c.mngt_IP }} for it until {{ formatDate(c.expires_at) }}.</p>
          <p v-if="c.detail" role="alert" class="mt-2 rounded-md border border-warning-300 bg-warning-50 p-3 text-sm text-warning-800">{{ c.detail }}</p>
          <ol class="mt-4 space-y-4">
            <li v-for="(step, i) in c.steps" :key="step.title">
              <h3 class="text-sm font-semibold text-gray-900">{{ i + 1 }}. {{ step.title }}</h3>
              <p class="text-sm text-gray-600">{{ step.where }}</p>
              <div class="mt-1 flex items-start gap-2">
                <pre class="flex-1 overflow-x-auto rounded-md bg-gray-900 p-3 text-xs text-gray-100" data-testid="commands">{{ step.commands.join('\n') }}</pre>
                <UiButton size="sm" @click="copyText(step.commands.join('\n'), 'Commands')">Copy</UiButton>
              </div>
              <p class="mt-1 text-xs text-gray-600">{{ step.note }}</p>
            </li>
          </ol>
          <div class="mt-4 flex gap-2">
            <UiButton variant="primary" :pending="acting === c.id" @click="done(c)">Done</UiButton>
            <UiButton :disabled="acting === c.id" @click="cancel(c)">Cancel</UiButton>
          </div>
        </template>
        <p v-else-if="c.state === 'onboarding'" class="mt-2 flex items-center gap-2 text-sm text-gray-700" role="status">
          <UiSpinner size="sm" /> {{ c.detail || 'BLab is taking it in.' }}
        </p>
        <div v-else class="mt-2 text-sm text-success-800" role="status">
          <p>It is in the lab and in service. Thank you.</p>
          <p v-if="c.switch" class="mt-1">
            More cables make it more useful:
            <router-link :to="{ path: '/contribute', query: { switch: c.switch } }" class="text-primary-700 underline">Contribute a cable</router-link>
          </p>
        </div>

        <div v-if="failures[c.id]" role="alert" class="mt-4 rounded-md border border-warning-300 bg-warning-50 p-3 text-sm text-warning-800" data-testid="contribution-failure">
          {{ failures[c.id] }}
        </div>
      </section>
    </template>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue';
import UiBadge from '../ui/UiBadge.vue';
import UiButton from '../ui/UiButton.vue';
import UiSpinner from '../ui/UiSpinner.vue';
import { switchContributionService } from '../../utils/apiService.js';
import { formatDate } from '../../utils/dateUtils.js';
import { copyText } from '../../utils/clipboard.js';
import { toast } from '../../composables/toast.js';
import { usePoll } from '../../composables/poll.js';

const STATES = {
  held: { label: 'Setting up', tone: 'primary' },
  onboarding: { label: 'Being taken in', tone: 'primary' },
  done: { label: 'In the lab', tone: 'success' },
};

const loading = ref(true);
const loadProblem = ref('');
const contributions = ref([]);
const models = ref([]);
const model = ref('');
const hasEmp = ref(true);
const starting = ref(false);
const acting = ref(null);
const failures = reactive({});

const load = async () => {
  const result = await switchContributionService.list();
  loading.value = false;
  if (result.success) {
    contributions.value = result.data.contributions;
    models.value = result.data.models;
  } else if (!contributions.value.length) {
    loadProblem.value = `Couldn't load your Contributions. ${result.message}`;
  }
  return result;
};
// While one is being taken in, its progress comes from the Switch worker
usePoll(load, 10000);

const start = async () => {
  starting.value = true;
  const result = await switchContributionService.start(model.value, hasEmp.value);
  starting.value = false;
  if (!result.success) {
    toast.error(result.message);
    return;
  }
  contributions.value = [result.data.contribution, ...contributions.value];
  model.value = '';
};

const replace = (updated) => {
  contributions.value = contributions.value.map(c => (c.id === updated.id ? updated : c));
};

const done = async (c) => {
  acting.value = c.id;
  delete failures[c.id];
  const result = await switchContributionService.done(c.id);
  acting.value = null;
  if (!result.success) {
    failures[c.id] = result.message;
    return;
  }
  replace(result.data.contribution);
};

const cancel = async (c) => {
  acting.value = c.id;
  const result = await switchContributionService.cancel(c.id);
  acting.value = null;
  if (!result.success) {
    failures[c.id] = result.message;
    return;
  }
  contributions.value = contributions.value.filter(x => x.id !== c.id);
  toast.success(result.data.detail);
};
</script>
