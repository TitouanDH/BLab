<template>
  <div class="flex flex-1 flex-col justify-center px-6 py-12 lg:px-8">
    <div class="sm:mx-auto sm:w-full sm:max-w-sm">
      <h2 class="text-center text-2xl font-bold tracking-tight text-gray-900">Log in to BLab</h2>
    </div>

    <div class="mt-10 sm:mx-auto sm:w-full sm:max-w-sm">
      <form class="space-y-6" @submit.prevent="handleLogin">
        <div>
          <label for="username" class="block text-sm font-medium text-gray-900">Username</label>
          <input id="username" v-model.trim="username" name="username" type="text" required autocomplete="username" class="input mt-2" />
        </div>
        <div>
          <label for="password" class="block text-sm font-medium text-gray-900">Password</label>
          <input id="password" v-model.trim="password" name="password" type="password" required autocomplete="current-password" class="input mt-2" />
        </div>
        <UiButton type="submit" variant="primary" class="w-full" :pending="pending">Log in</UiButton>
      </form>

      <p class="mt-10 text-center text-sm text-gray-500">
        No account yet?
        <router-link :to="{ path: '/signup', query: route.query }" class="font-semibold text-primary-700 hover:text-primary-800">Sign up</router-link>
      </p>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { login, safeRedirect } from '../auth';
import UiButton from '../components/ui/UiButton.vue';
import { toast } from '../composables/toast.js';

const username = ref('');
const password = ref('');
const pending = ref(false);
const router = useRouter();
const route = useRoute();  // ?redirect: the page asked for before logging in, such as an Invite link

const handleLogin = async () => {
  pending.value = true;
  const result = await login(username.value, password.value);
  pending.value = false;
  if (result.success) {
    router.push(safeRedirect(route.query.redirect));
  } else {
    toast.error(result.message);
    password.value = '';
  }
};
</script>
