<template>
  <!-- Which Project a Reserve goes into (with Projects on): the last used one is preselected,
       "New Project…" names one inline, and with none yet BLab makes the user their first. -->
  <div class="flex flex-wrap items-center gap-2" data-testid="project-choice">
    <label :for="id" class="text-sm font-medium text-gray-700">Reserve into</label>
    <select :id="id" v-model="model.choice" class="input w-auto">
      <option v-if="!projects.length" value="">A new Project for you</option>
      <option v-for="p in projects" :key="p.id" :value="String(p.id)">{{ p.name }}</option>
      <option value="new">New Project…</option>
    </select>
    <template v-if="model.choice === 'new'">
      <input v-model.trim="model.name" maxlength="100" placeholder="Name" aria-label="New Project name" class="input w-48" />
      <input v-model.trim="model.reference" maxlength="100" placeholder="Reference (optional)" aria-label="New Project reference" class="input w-48" />
    </template>
  </div>
</template>

<script setup>
// The choice itself lives in the page (projectChoice() below), so the quick Reserve and the
// "Reserve until…" dialog share it
defineProps({
  id: { type: String, default: 'reserve-project' },
  projects: { type: Array, required: true },  // the user's active Projects
});
const model = defineModel({ type: Object, required: true });  // { choice: '' | 'new' | '<id>', name, reference }
</script>
