<template>
  <!-- The Snapshots section of a Project's page (CONTEXT.md): Save with a note, the last ones BLab
       keeps, and Restore onto the same Switches with its progress per Switch. -->
  <div class="container mx-auto max-w-5xl px-4 py-6">
    <div class="mb-6">
      <h2 class="sr-only">Snapshots</h2>
      <p class="text-sm text-gray-600">
        A Snapshot holds each Switch's saved config (<span class="font-mono">working/vcboot.cfg</span>: save yours
        with <span class="font-mono">write memory</span> first) and its AOS release, the Project's Links and its layout.
        BLab takes one before every Release<span v-if="kept">; the last {{ kept }} are kept</span>.
      </p>
    </div>

    <div v-if="!loaded" class="flex justify-center py-16 text-primary-700"><UiSpinner size="lg" /></div>
    <template v-else>
      <!-- Save -->
      <form v-if="!project.archived_at" class="mb-6 flex flex-wrap items-end gap-2 rounded-lg bg-white p-4 shadow-sm ring-1 ring-gray-200" @submit.prevent="save">
        <div class="min-w-[16rem] flex-1">
          <label for="snapshot-note" class="mb-1 block text-sm font-medium text-gray-700">Note (optional)</label>
          <input id="snapshot-note" v-model.trim="note" maxlength="200" placeholder="What this setup is, say" class="input" />
        </div>
        <UiButton
          type="submit" variant="primary" :pending="saving" :disabled="!project.switch_count || underWay"
          :title="!project.switch_count ? 'The Project holds no Switch' : underWay ? 'A Restore is under way' : 'Save a Snapshot of every Switch now'"
        >Save</UiButton>
      </form>

      <!-- The latest Restore, Switch by Switch -->
      <section v-if="restore" class="mb-6 rounded-lg bg-white p-4 shadow-sm ring-1 ring-gray-200" data-testid="restore-progress" aria-labelledby="restore-heading">
        <h2 id="restore-heading" class="text-lg font-semibold text-gray-900">
          {{ underWay ? 'Restore under way' : 'Last Restore' }}
          <UiSpinner v-if="underWay" size="sm" class="ml-1 inline text-primary-700" />
        </h2>
        <p class="text-sm text-gray-600">
          Started {{ formatDate(restore.started_at) }}<span v-if="restore.started_by"> by {{ restore.started_by }}</span><span v-if="restore.finished_at">, done {{ formatDate(restore.finished_at) }}</span>.
        </p>
        <ul class="mt-2 divide-y divide-gray-100 text-sm">
          <li v-for="s in restore.switches" :key="s.id" :data-switch="s.id" class="flex flex-wrap items-baseline gap-x-3 py-1.5">
            <span class="w-40 font-medium">{{ switchLabel(s) }}</span>
            <UiBadge :tone="PROGRESS[s.state].tone">{{ PROGRESS[s.state].label }}</UiBadge>
            <span class="text-gray-600">{{ s.detail }}<span v-if="s.free_on"> Free on {{ formatDate(s.free_on) }}.</span></span>
            <span v-for="w in s.warnings" :key="w" class="basis-full pl-40 text-warning-800">{{ w }}</span>
          </li>
        </ul>
        <template v-if="restore.finished_at && restore.links.length">
          <h3 class="mt-3 text-sm font-semibold text-gray-700">Links</h3>
          <ul class="text-sm">
            <li v-for="(l, i) in restore.links" :key="i" class="py-0.5">
              {{ linkLabel(l.ends) }}: <span :class="l.outcome === 'failed' ? 'text-warning-800' : 'text-gray-600'">{{ l.detail }}</span>
            </li>
          </ul>
        </template>
      </section>

      <p v-if="!snapshots.length" class="rounded-lg bg-white p-8 text-center text-gray-600 shadow-sm ring-1 ring-gray-200" data-testid="no-snapshots">
        No Snapshot yet. Save one, or Release a Switch: BLab takes one first.
      </p>
      <ul class="space-y-3">
        <li v-for="s in snapshots" :key="s.id" :data-snapshot="s.id" class="rounded-lg bg-white p-4 shadow-sm ring-1 ring-gray-200">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 class="font-semibold text-gray-900">
                {{ formatDate(s.taken_at) }}
                <UiBadge class="ml-1" :tone="s.reason === 'save' ? 'primary' : 'neutral'">
                  {{ s.reason === 'save' ? `Saved${s.taken_by ? ` by ${s.taken_by}` : ''}` : 'Before Release' }}
                </UiBadge>
              </h2>
              <p v-if="s.note" class="text-sm text-gray-800">{{ s.note }}</p>
              <p class="text-sm text-gray-600">
                {{ s.switches.length }} Switch{{ s.switches.length === 1 ? '' : 'es' }} · {{ s.links }} Link{{ s.links === 1 ? '' : 's' }}
              </p>
            </div>
            <UiButton v-if="!project.archived_at" size="sm" :disabled="underWay" :title="underWay ? 'A Restore is under way' : 'Set these Switches up again'" @click="openRestore(s)">Restore…</UiButton>
          </div>
          <ul class="mt-2 flex flex-wrap gap-2 text-xs">
            <li v-for="sw in s.switches" :key="sw.id" :title="sw.read_error || ''">
              <!-- Its saved config, on the Switch's Config -->
              <router-link
                :to="{ path: `/switches/${sw.id}/config`, query: { show: `snapshot-${s.id}` } }" :data-config="sw.id"
                class="block rounded bg-gray-50 px-2 py-1 ring-1 ring-gray-200 hover:bg-gray-100 hover:ring-primary-300"
                :aria-label="`Config of ${switchLabel(sw)} in this Snapshot`"
              >
                {{ switchLabel(sw) }} <span class="text-gray-500">{{ sw.aos_release || '' }}</span>
                <span v-if="!sw.restorable" class="text-warning-800"> (not read)</span>
              </router-link>
            </li>
          </ul>
        </li>
      </ul>
    </template>

    <!-- Restore: what it would do, then confirm -->
    <UiModal v-if="restoring" title="Restore this Snapshot?" width="lg" @close="restoring = null">
      <div v-if="!plan" class="flex justify-center py-8 text-primary-700"><UiSpinner /></div>
      <template v-else>
        <p class="mb-3">
          Free Switches are reserved into {{ project.name }} for {{ DEFAULT_RESERVATION_DAYS }} days. Each chosen Switch gets
          its config from the Snapshot and is reloaded, which ends any session on it. Then its {{ plan.links }}
          Link{{ plan.links === 1 ? '' : 's' }} between the Switches Restored are built again.
        </p>
        <ul class="divide-y divide-gray-100" data-testid="restore-plan">
          <li v-for="s in plan.switches" :key="s.id" :data-switch="s.id" class="flex items-start gap-3 py-2">
            <input
              :id="`restore-${s.id}`" v-model="chosen" type="checkbox" :value="s.id" class="mt-1"
              :disabled="!s.restorable"
            />
            <div class="flex-1">
              <label :for="`restore-${s.id}`" class="block">
                <span class="font-medium">{{ switchLabel(s) }}</span>
                <span class="ml-2 text-gray-600">{{ s.detail }}<span v-if="s.free_on"> Free on {{ formatDate(s.free_on) }}.</span></span>
                <span v-if="s.release_installed" class="block text-gray-600" data-testid="release-install">
                  The Snapshot was taken on AOS {{ s.snapshot_release }}, the Switch runs {{ s.current_release }}:
                  BLab installs {{ s.snapshot_release }} first, which adds 10 to 20 minutes.
                </span>
                <span v-else-if="s.release_differs" class="block text-warning-800" data-testid="release-warning">
                  AOS release differs: the Snapshot was taken on {{ s.snapshot_release }}, which BLab can't install on it;
                  the Switch runs {{ s.current_release }}. Its config is loaded anyway.
                </span>
              </label>
              <!-- A busy Switch: a free one can stand in for it (#46) -->
              <span v-if="s.replacements && s.replacements.length" class="mt-1 block">
                <span class="mr-2 text-gray-700">Replace with</span>
                <select :aria-label="`Replace ${switchLabel(s)} with`" class="input inline-block w-auto py-1" :value="replacing[s.id] || ''" @change="pickReplacement(s, $event.target.value)">
                  <option value="">None: leave it out</option>
                  <option v-for="c in s.replacements" :key="c.id" :value="c.id">
                    {{ switchLabel(c) }} ({{ RANKS[c.rank] }}, {{ c.cabled }} cabled port{{ c.cabled === 1 ? '' : 's' }} free)
                  </option>
                </select>
              </span>
              <span v-else-if="s.state === 'busy' || s.state === 'unavailable'" class="mt-1 block text-gray-500">
                No free Switch has enough cabled ports to replace it.
              </span>
              <span v-if="replacing[s.id] && !previews[s.id]" class="mt-1 block text-primary-700"><UiSpinner size="sm" class="inline" /> Rewriting its config…</span>
              <span v-if="previews[s.id]" class="mt-1 block" :data-preview="s.id">
                <span class="block text-gray-700">
                  Ports: <span v-for="(to, from) in previews[s.id].port_map" :key="from" class="mr-2 font-mono">{{ from }} → {{ to }}</span>
                </span>
                <span v-if="previews[s.id].release_installed" class="block text-gray-600">
                  It runs AOS {{ previews[s.id].current_release }}: BLab installs {{ s.snapshot_release }} first, which adds 10 to 20 minutes.
                </span>
                <span v-for="n in previews[s.id].notes" :key="n" class="block text-warning-800">{{ n }}</span>
                <details class="mt-1" open>
                  <summary class="cursor-pointer text-gray-700">Its config, rewritten: {{ changed(previews[s.id]) }} line{{ changed(previews[s.id]) === 1 ? '' : 's' }} changed</summary>
                  <pre class="mt-1 max-h-64 overflow-auto rounded bg-gray-50 p-2 font-mono text-xs ring-1 ring-gray-200"><span v-for="(line, i) in [...previews[s.id].diff, ...previews[s.id].vcsetup_diff]" :key="i" :class="diffClass(line)">{{ line }}
</span></pre>
                </details>
              </span>
            </div>
          </li>
        </ul>
      </template>
      <template #actions>
        <UiButton @click="restoring = null">Cancel</UiButton>
        <UiButton variant="primary" :pending="starting" :disabled="!plan || !restoreCount || previewing" @click="startRestore">
          Restore {{ restoreCount }} Switch{{ restoreCount === 1 ? '' : 'es' }}
        </UiButton>
      </template>
    </UiModal>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue';
import UiBadge from '../../components/ui/UiBadge.vue';
import UiButton from '../../components/ui/UiButton.vue';
import UiModal from '../../components/ui/UiModal.vue';
import UiSpinner from '../../components/ui/UiSpinner.vue';
import { projectService } from '../../utils/apiService.js';
import { DEFAULT_RESERVATION_DAYS } from '../../utils/constants.js';
import { formatDate } from '../../utils/dateUtils.js';
import { switchLabel } from '../../utils/switchLabel.js';
import { toast } from '../../composables/toast.js';
import { isUnreachable, latestCall, usePoll } from '../../composables/poll.js';

// How each Switch of a Restore is getting on (api/api/restore.py)
const PROGRESS = {
  left_out: { label: 'Left out', tone: 'neutral' },
  waiting: { label: 'Waiting', tone: 'primary' },
  reloading: { label: 'Reloading', tone: 'primary' },
  done: { label: 'Done', tone: 'success' },
  failed: { label: 'Failed', tone: 'strong-warning' },
};

const props = defineProps({
  project: { type: Object, required: true },  // as projects/<id>/ gives it (Project.vue)
});
// changed: a Restore reserved Switches into the Project
const emit = defineEmits(['changed']);

const projectId = props.project.id;
const loaded = ref(false);
const snapshots = ref([]);
const kept = ref(null);
const restore = ref(null);
const underWay = computed(() => !!restore.value && !restore.value.finished_at);

// Switch names: the Snapshots know each Switch's model (and its IP, when they give it)
const known = computed(() => {
  const byId = {};
  for (const s of snapshots.value) for (const sw of s.switches) byId[sw.id] = sw;
  for (const sw of restore.value?.switches || []) byId[sw.id] = byId[sw.id] || sw;  // a replacement (#46)
  return byId;
});
const linkLabel = (ends) => ends.map(e => `${switchLabel(known.value[e.switch] || { model: 'Switch' })} ${e.port}`).join(' ↔ ');

// A refresh after an action can overtake a poll in flight: only the latest answer is shown (#85)
const latest = latestCall();
const load = async () => {
  const isLatest = latest();
  const listing = await projectService.snapshots(projectId);
  if (!isLatest()) return [];
  if (listing.status === 404) {
    emit('changed');  // no longer ours: the page says so
    return [];
  }
  if (listing.success) {
    snapshots.value = listing.data.snapshots;
    kept.value = listing.data.kept;
    restore.value = listing.data.restore;
    loaded.value = true;
  } else if (!isUnreachable(listing)) {
    toast.error(`Couldn't load the Snapshots. ${listing.message}`, 'snapshots-load');
  }
  return [listing];
};
const { refresh } = usePoll(load, 5 * 1000);

// --- Save ---
const note = ref('');
const saving = ref(false);
const save = async () => {
  saving.value = true;
  const result = await projectService.saveSnapshot(projectId, note.value);
  saving.value = false;
  if (!result.success) {
    toast.error(`Couldn't save. ${result.message}`);
    return;
  }
  const unread = result.data.switches.filter(s => !s.restorable);
  if (unread.length) toast.error(`Saved, but BLab couldn't read ${unread.map(switchLabel).join(', ')}: ${unread[0].read_error}`);
  else toast.success('Saved.');
  note.value = '';
  refresh();
};

// --- Restore ---
const restoring = ref(null);
const plan = ref(null);
const chosen = ref([]);
const starting = ref(false);
// Replacements for busy Switches (#46): { <busy id>: <free id> }, and what each would load
const RANKS = { same_model: 'same model', same_family: 'same family', other: 'another model' };
const replacing = ref({});
const previews = ref({});
const restoreCount = computed(() => chosen.value.length + Object.keys(replacing.value).length);
const previewing = computed(() => Object.keys(replacing.value).some(id => !previews.value[id]));
const changed = (preview) => [...preview.diff, ...preview.vcsetup_diff].filter(l => l.startsWith('+')).length;
const diffClass = (line) => (line.startsWith('+') ? 'text-success-800' : line.startsWith('-') ? 'text-warning-800'
  : line.startsWith('@@') ? 'text-gray-400' : 'text-gray-600');
const without = (map, id) => Object.fromEntries(Object.entries(map).filter(([key]) => key !== String(id)));

const pickReplacement = async (s, value) => {
  previews.value = without(previews.value, s.id);
  if (!value) {
    replacing.value = without(replacing.value, s.id);
    return;
  }
  const by = Number(value);
  // A Switch stands in for one Switch only: picked here, it is no longer picked elsewhere
  const others = Object.entries(replacing.value).filter(([key, picked]) => picked === by && key !== String(s.id));
  for (const [key] of others) previews.value = without(previews.value, key);
  replacing.value = { ...Object.fromEntries(Object.entries(replacing.value).filter(([, picked]) => picked !== by)), [s.id]: by };
  const snapshot = restoring.value;
  const result = await projectService.restorePreview(projectId, snapshot.id, s.id, by);
  if (restoring.value !== snapshot || replacing.value[s.id] !== by) return;
  if (!result.success) {
    replacing.value = without(replacing.value, s.id);
    toast.error(`Couldn't rewrite its config. ${result.message}`);
    return;
  }
  previews.value = { ...previews.value, [s.id]: result.data };
};

const openRestore = async (snapshot) => {
  restoring.value = snapshot;
  plan.value = null;
  replacing.value = {};
  previews.value = {};
  const result = await projectService.restorePlan(projectId, snapshot.id);
  if (restoring.value !== snapshot) return;
  if (!result.success) {
    restoring.value = null;
    toast.error(`Couldn't prepare the Restore. ${result.message}`);
    return;
  }
  plan.value = result.data;
  chosen.value = result.data.switches.filter(s => s.restorable).map(s => s.id);
};

const startRestore = async () => {
  starting.value = true;
  const result = await projectService.restore(projectId, restoring.value.id, chosen.value, replacing.value);
  starting.value = false;
  if (!result.success) {
    toast.error(`Couldn't start the Restore. ${result.message}`);
    return;
  }
  restoring.value = null;
  restore.value = result.data;
  toast.success('Restore started: BLab reloads each Switch, then builds the Links again.');
  refresh();
  emit('changed');
};
</script>
