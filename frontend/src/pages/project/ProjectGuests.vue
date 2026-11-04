<template>
  <!-- The Guests of a Project (#96, CONTEXT.md), under its Members: logins for people without a
       BLab account. Each gets a read-write Switch account on the Project's Switches; the member
       who added it is accountable for it. Shown only while this stage has Guests on. -->
  <section class="mt-8" aria-labelledby="guests-title">
    <h3 id="guests-title" class="mb-1 text-base font-semibold text-gray-900">Guests</h3>
    <p class="mb-4 text-sm text-gray-600">
      A guest logs in to the Project's Switches with the name and password you choose, until they are Released
      or the guest is deleted. You are accountable for the guests you add.
    </p>

    <ul v-if="project.guests.length" class="mb-4 divide-y divide-gray-100 rounded-lg bg-white text-sm shadow-sm ring-1 ring-gray-200" aria-label="Guests">
      <li v-for="g in project.guests" :key="g.id" :data-guest="g.name" class="flex items-center justify-between gap-2 px-4 py-2">
        <span>
          <span class="font-mono">{{ g.name }}</span>
          <span class="ml-2 text-gray-500">added by {{ g.added_by?.username || 'a former member' }}, {{ formatDate(g.added_at) }}</span>
        </span>
        <UiButton v-if="g.may_delete" size="sm" variant="danger" @click="toDelete = g">Delete</UiButton>
      </li>
    </ul>
    <p v-else class="mb-4 text-sm text-gray-500">No guests.</p>

    <form v-if="!project.archived_at" class="flex flex-wrap items-end gap-3 rounded-lg bg-white p-4 shadow-sm ring-1 ring-gray-200" aria-label="Add a guest" @submit.prevent="add">
      <div>
        <label for="guest-name" class="block text-sm font-medium text-gray-700">Name</label>
        <input id="guest-name" v-model.trim="name" required maxlength="63" autocomplete="off" class="input mt-1 w-48" />
      </div>
      <div>
        <label for="guest-password" class="block text-sm font-medium text-gray-700">Password</label>
        <input id="guest-password" v-model="password" required type="password" autocomplete="new-password" class="input mt-1 w-48" aria-describedby="guest-password-rules" />
      </div>
      <UiButton type="submit" variant="primary" :pending="adding" :disabled="!name || !password">Add guest</UiButton>
      <p id="guest-password-rules" class="w-full text-xs text-gray-500">
        The password follows the Switch password rules: 8 to 30 characters, an upper and a lower case letter, a digit
        and a symbol, without spaces, ! " or ', and not containing the guest's name.
      </p>
      <p v-if="error" class="w-full text-sm text-red-700" role="alert">{{ error }}</p>
    </form>

    <UiConfirm
      v-if="toDelete" :title="`Delete ${toDelete.name}?`" confirm-label="Delete" danger :pending="deleting"
      :message="`BLab removes the guest's Switch accounts on the Project's Switches.`"
      @close="toDelete = null" @confirm="remove"
    />
  </section>
</template>

<script setup>
import { ref } from 'vue';
import UiButton from '../../components/ui/UiButton.vue';
import UiConfirm from '../../components/ui/UiConfirm.vue';
import { projectService } from '../../utils/apiService.js';
import { formatDate } from '../../utils/dateUtils.js';
import { toast } from '../../composables/toast.js';

const props = defineProps({
  project: { type: Object, required: true },  // as projects/<id>/ gives it, with guests
});
const emit = defineEmits(['changed']);

const name = ref('');
const password = ref('');
const adding = ref(false);
const error = ref('');

const add = async () => {
  adding.value = true;
  error.value = '';
  const result = await projectService.addGuest(props.project.id, name.value, password.value);
  adding.value = false;
  if (!result.success) {
    error.value = result.message;
    return;
  }
  toast.success(result.data.detail);
  name.value = '';
  password.value = '';
  emit('changed');
};

const toDelete = ref(null);
const deleting = ref(false);
const remove = async () => {
  deleting.value = true;
  const result = await projectService.deleteGuest(props.project.id, toDelete.value.id);
  deleting.value = false;
  const guest = toDelete.value.name;
  toDelete.value = null;
  if (!result.success) {
    toast.error(`Couldn't delete ${guest}. ${result.message}`);
    return;
  }
  toast.success(result.data.detail);
  emit('changed');
};
</script>
