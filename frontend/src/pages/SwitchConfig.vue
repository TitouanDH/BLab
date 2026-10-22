<template>
  <!-- A Switch's Config (#44), for the members of its Project and admins, read-only: its live
       config (the running config, read as admin; BLab reads it at most every 30 seconds per
       Switch and shows the last read meanwhile), its Golden init, and each Snapshot's saved
       config. Any one is shown, and, if asked, how it differs from another. The choice is in
       the address (?show=...&against=...), so it can be shared with another member. -->
  <div class="flex min-h-0 flex-1 flex-col">
    <div class="border-b border-gray-200 bg-white px-4 pt-3 lg:px-8">
      <router-link :to="back.to" class="text-sm text-primary-700 hover:underline" data-testid="config-back">&larr; {{ back.label }}</router-link>
      <div class="flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
        <div class="min-w-0 pb-2">
          <h1 class="truncate text-xl font-bold text-gray-900">{{ data ? switchLabel(data.switch) : 'Switch' }}</h1>
          <p class="truncate text-sm text-gray-600">Read-only: BLab only reads the Switch, never changes it here.</p>
        </div>
        <nav class="-mb-px flex gap-1 overflow-x-auto" aria-label="Switch sections">
          <router-link
            :to="`/switches/${switchId}/config`" aria-current="page"
            class="whitespace-nowrap border-b-2 border-primary-600 px-3 py-2 text-sm font-semibold text-primary-800"
          >Config</router-link>
        </nav>
      </div>
    </div>

    <div class="container mx-auto max-w-6xl px-4 py-6">
      <p v-if="refused" class="rounded-lg bg-white p-8 text-center text-gray-600 shadow-sm ring-1 ring-gray-200" data-testid="config-refused">
        {{ refused }} <router-link to="/" class="font-medium text-primary-700 underline">Back to My lab</router-link>
      </p>
      <div v-else-if="!data" class="flex justify-center py-16 text-primary-700"><UiSpinner size="lg" /></div>
      <template v-else>
        <!-- What is shown, and what it is compared with -->
        <div class="mb-4 flex flex-wrap items-end gap-3 rounded-lg bg-white p-4 shadow-sm ring-1 ring-gray-200">
          <div class="min-w-[14rem] flex-1">
            <label for="config-show" class="mb-1 block text-sm font-medium text-gray-700">Show</label>
            <select id="config-show" v-model="show" class="input">
              <option v-for="s in data.sources" :key="s.key" :value="s.key">{{ sourceLabel(s) }}</option>
            </select>
          </div>
          <div class="min-w-[14rem] flex-1">
            <label for="config-against" class="mb-1 block text-sm font-medium text-gray-700">Compare with</label>
            <select id="config-against" v-model="against" class="input">
              <option value="">Nothing</option>
              <option v-for="s in data.sources.filter(x => x.key !== show)" :key="s.key" :value="s.key">{{ sourceLabel(s) }}</option>
            </select>
          </div>
          <div class="flex items-center gap-2">
            <UiSpinner v-if="loading" size="sm" class="text-primary-700" />
            <UiButton
              v-if="liveInView" :disabled="secondsToRead > 0 || loading" :title="readAgainTitle" data-testid="read-again" @click="load"
            >{{ secondsToRead > 0 ? `Read again in ${secondsToRead} s` : 'Read again' }}</UiButton>
            <UiButton v-if="shown?.config" @click="copyText(shown.config, 'Config', { reveal: false })">Copy</UiButton>
          </div>
        </div>

        <!-- Where the config shown comes from -->
        <p v-if="shown" class="mb-3 text-sm text-gray-600" data-testid="config-about">{{ about(shown) }}</p>
        <p v-if="shown?.reading" class="mb-3 flex items-center gap-2 text-sm text-gray-600" data-testid="config-reading">
          <UiSpinner size="sm" class="text-primary-700" /> BLab is reading it...
        </p>
        <p v-for="side in unreadable" :key="side.key" class="mb-3 rounded-md bg-warning-50 p-3 text-sm text-warning-800 ring-1 ring-warning-200" data-testid="config-error">
          {{ sourceName(side) }} couldn't be read: {{ side.error }}
        </p>

        <!-- The diff: lines only in the config shown (+) or only in the one it is compared with (-) -->
        <section v-if="data.diff" aria-label="Differences" data-testid="config-diff">
          <p class="mb-2 text-sm font-medium text-gray-800" data-testid="diff-summary">{{ diffSummary }}</p>
          <div class="overflow-x-auto rounded-lg bg-white font-mono text-xs leading-5 shadow-sm ring-1 ring-gray-200">
            <template v-for="block in blocks" :key="block.start">
              <button
                v-if="block.folded" type="button" :data-fold="block.start"
                class="block w-full bg-gray-50 px-3 py-1 text-left font-sans text-xs font-medium text-primary-700 hover:bg-gray-100"
                @click="unfold(block.start)"
              >Show {{ plural(block.rows.length, 'unchanged line', 'unchanged lines') }}</button>
              <div
                v-for="(row, i) in block.folded ? [] : block.rows" :key="block.start + i" :data-op="OPS[row.op].name"
                :class="['flex whitespace-pre', OPS[row.op].row]"
              ><span :class="['w-6 shrink-0 select-none text-center', OPS[row.op].mark]" aria-hidden="true">{{ row.op }}</span><span class="sr-only">{{ OPS[row.op].spoken }}</span><span class="pr-3">{{ row.line }}</span></div>
            </template>
          </div>
        </section>

        <!-- The config alone, with line numbers -->
        <section v-else-if="shown?.config" aria-label="Config" data-testid="config-text">
          <div class="overflow-x-auto rounded-lg bg-white font-mono text-xs leading-5 shadow-sm ring-1 ring-gray-200">
            <div v-for="(line, i) in lines" :key="i" class="flex whitespace-pre"><span class="w-12 shrink-0 select-none pr-3 text-right text-gray-400" aria-hidden="true">{{ i + 1 }}</span><span class="pr-3">{{ line }}</span></div>
          </div>
        </section>

        <details v-if="shown?.vcsetup" class="mt-4 rounded-lg bg-white p-4 shadow-sm ring-1 ring-gray-200">
          <summary class="cursor-pointer text-sm font-medium text-gray-800">Its VC setup (<span class="font-mono">working/vcsetup.cfg</span>)</summary>
          <pre class="mt-2 overflow-x-auto font-mono text-xs leading-5">{{ shown.vcsetup }}</pre>
        </details>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import UiButton from '../components/ui/UiButton.vue';
import UiSpinner from '../components/ui/UiSpinner.vue';
import { switchConfigService } from '../utils/apiService.js';
import { copyText } from '../utils/clipboard.js';
import { formatDate } from '../utils/dateUtils.js';
import { plural } from '../utils/plural.js';
import { switchLabel } from '../utils/switchLabel.js';
import { toast } from '../composables/toast.js';

const CONTEXT = 3;  // unchanged lines kept around each change; longer runs fold
const READING_RETRY_MS = 3000;  // another member's read is under way: look again this soon
// Added lines in the primary colour, removed ones greyed: red and green are kept for actions and status (tailwind.config.js)
const OPS = {
  ' ': { name: 'same', row: 'text-gray-800', mark: 'text-gray-300', spoken: '' },
  '+': { name: 'added', row: 'bg-primary-50 text-primary-900', mark: 'font-bold text-primary-700', spoken: 'Added: ' },
  '-': { name: 'removed', row: 'bg-gray-100 text-gray-600', mark: 'font-bold text-gray-700', spoken: 'Removed: ' },
};

const route = useRoute();
const router = useRouter();
const switchId = computed(() => String(route.params.id));

const data = ref(null);
const loading = ref(false);
const refused = ref('');
const unfolded = ref(new Set());
const now = ref(Date.now());

// The choice lives in the address; changing it loads again
const show = computed({
  get: () => route.query.show || data.value?.shown?.key || '',
  set: (key) => router.replace({ query: { ...route.query, show: key, against: route.query.against === key ? undefined : route.query.against } }),
});
const against = computed({
  get: () => route.query.against || '',
  set: (key) => router.replace({ query: { ...route.query, against: key || undefined } }),
});

const shown = computed(() => data.value?.shown || null);
const sides = computed(() => [data.value?.shown, data.value?.against].filter(Boolean));  // what is shown, and what it is compared with
const lines = computed(() => (shown.value?.config || '').replace(/\n$/, '').split('\n'));
const unreadable = computed(() => sides.value.filter(side => side.config === null && side.error));

// The back link: to the Switch's Project, for its members, else My lab
const back = computed(() => {
  const project = data.value?.switch?.project;
  return project ? { to: `/projects/${project.id}/switches`, label: project.name } : { to: '/', label: 'My lab' };
});

const sourceName = (side) => {
  const source = data.value?.sources.find(s => s.key === side.key);
  return source ? sourceLabel(source) : side.key;
};
const sourceLabel = (s) => {
  if (s.kind === 'live') return 'Live (running config)';
  if (s.kind === 'golden') return `Golden init (${s.model})`;
  const why = s.reason === 'save' ? `Saved${s.taken_by ? ` by ${s.taken_by}` : ''}` : 'Before Release';
  const elsewhere = s.project.id !== data.value?.switch?.project?.id ? ` · ${s.project.name}` : '';
  return `Snapshot of ${formatDate(s.taken_at)} · ${why}${s.note ? `: ${s.note}` : ''}${elsewhere}`;
};
const about = (side) => {
  if (side.kind === 'live') {
    if (!side.read_at) return 'The running config (show configuration snapshot), changes not yet saved included.';
    return `The running config (show configuration snapshot), changes not yet saved included, read ${formatDate(side.read_at)}`
      + `${side.read_by ? ` for ${side.read_by}` : ''}.`;
  }
  if (side.kind === 'golden') {
    return `The lab's template for ${data.value.switch.model}: what init holds once a Cleanup has repaired it.`;
  }
  return `The saved config (working/vcboot.cfg) as the Snapshot holds it, read ${formatDate(side.read_at)}`
    + `${side.aos_release ? ` on AOS ${side.aos_release}` : ''}.`;
};

// Live reads: at most one per Switch every live_read_interval seconds
const liveInView = computed(() => sides.value.some(side => side.kind === 'live'));
const nextReadAt = computed(() => {
  const side = sides.value.find(s => s.kind === 'live');
  return side ? new Date(side.next_read_at).getTime() : 0;
});
// Never more than the interval: this browser's clock may run behind BLab's
const secondsToRead = computed(() => Math.min(data.value?.live_read_interval || 0,
  Math.max(0, Math.ceil((nextReadAt.value - now.value) / 1000))));
const readAgainTitle = computed(() => `BLab reads a Switch's live config at most every ${data.value?.live_read_interval} seconds`);
const ticker = setInterval(() => { now.value = Date.now(); }, 1000);

const diffSummary = computed(() => {
  const d = data.value.diff;
  const other = sourceName(data.value.against);
  if (!d.added && !d.removed) return d.reordered ? `The same lines as ${other}, in another order.` : `No difference with ${other}.`;
  return `${plural(d.added, 'line', 'lines')} only here (+), ${plural(d.removed, 'line', 'lines')} only in ${other} (-).`;
});

// The diff's rows, unchanged runs longer than the context around them folded
const blocks = computed(() => {
  const rows = data.value?.diff?.rows || [];
  const result = [];
  let start = 0;
  while (start < rows.length) {
    let end = start;
    const same = rows[start].op === ' ';
    while (end < rows.length && (rows[end].op === ' ') === same) end += 1;
    const run = rows.slice(start, end);
    const head = start === 0 ? 0 : CONTEXT;
    const tail = end === rows.length ? 0 : CONTEXT;
    if (same && run.length > head + tail + 1 && !unfolded.value.has(start + head)) {
      if (head) result.push({ start, rows: run.slice(0, head) });
      result.push({ start: start + head, rows: run.slice(head, run.length - tail), folded: true });
      if (tail) result.push({ start: end - tail, rows: run.slice(run.length - tail) });
    } else {
      result.push({ start, rows: run });
    }
    start = end;
  }
  return result;
});
const unfold = (start) => { unfolded.value = new Set([...unfolded.value, start]); };

let retry = null;
let asked = 0;  // the latest load: an older answer is dropped
const load = async () => {
  clearTimeout(retry);
  const id = switchId.value;
  const query = { show: route.query.show || null, against: route.query.against || null };
  const mine = ++asked;
  loading.value = true;
  const result = await switchConfigService.get(id, query);
  if (mine !== asked) return;
  loading.value = false;
  if (result.success) {
    data.value = result.data;
    refused.value = '';
    unfolded.value = new Set();
    if (result.data.shown?.reading || result.data.against?.reading) retry = setTimeout(load, READING_RETRY_MS);
  } else if (result.status === 403 || result.status === 404) {
    // Neither a member of its Project nor an admin, or a Snapshot since pruned
    refused.value = result.message;
  } else {
    toast.error(`Couldn't load the config. ${result.message}`, 'switch-config');
  }
};

watch(() => [route.params.id, route.query.show, route.query.against], () => {
  if (route.params.id) load();
}, { immediate: true });

onBeforeUnmount(() => {
  clearInterval(ticker);
  clearTimeout(retry);
});
</script>
