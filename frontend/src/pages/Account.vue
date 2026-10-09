<template>
  <div class="flex flex-1 flex-col justify-center px-6 py-12 lg:px-8">
    <div class="sm:mx-auto sm:w-full sm:max-w-sm">
      <h2 class="text-center text-2xl font-bold tracking-tight text-gray-900">Your account</h2>
      <p v-if="required" class="mt-4 text-center text-sm text-gray-700">
        BLab needs your email address before you go on: your Rainbow login, so that BLab can message you there.
      </p>
    </div>

    <div class="mt-10 sm:mx-auto sm:w-full sm:max-w-sm">
      <UiSpinner v-if="loading" class="mx-auto" />
      <form v-else class="space-y-6" @submit.prevent="save">
        <div>
          <span class="block text-sm font-medium text-gray-900">Username</span>
          <p class="mt-2 text-sm text-gray-700">{{ username }}</p>
        </div>
        <div>
          <label for="email" class="block text-sm font-medium text-gray-900">Email</label>
          <input id="email" v-model.trim="email" name="email" type="email" required autocomplete="email" class="input mt-2" aria-describedby="email-help" />
          <p id="email-help" class="mt-1 text-xs text-gray-500">Your Rainbow login: BLab messages you there.</p>
        </div>
        <UiButton type="submit" variant="primary" class="w-full" :pending="pending">Save</UiButton>
      </form>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { getAccount, saveEmail } from '../auth';
import UiButton from '../components/ui/UiButton.vue';
import UiSpinner from '../components/ui/UiSpinner.vue';
import { toast } from '../composables/toast.js';

// The user's own account. An account without its email lands here first (router.js) and
// goes on to My lab once it is saved; anyone can come back to change it.
const router = useRouter();
const username = ref('');
const email = ref('');
const required = ref(false);
const loading = ref(true);
const pending = ref(false);

onMounted(async () => {
  const result = await getAccount();
  loading.value = false;
  if (!result.success) {
    toast.error(result.message, 'account');
    return;
  }
  username.value = result.data.username;
  email.value = result.data.email;
  required.value = !result.data.email;
});

const save = async () => {
  pending.value = true;
  const result = await saveEmail(email.value);
  pending.value = false;
  if (!result.success) {
    toast.error(result.message, 'account');
    return;
  }
  email.value = result.data.email;
  toast.success('Email saved.');
  if (required.value) router.push('/');
};
</script>
