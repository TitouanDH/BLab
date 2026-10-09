<template>
  <div class="flex flex-1 flex-col justify-center px-6 py-12 lg:px-8">
    <div class="sm:mx-auto sm:w-full sm:max-w-sm">
      <h2 class="text-center text-2xl font-bold tracking-tight text-gray-900">Create your account</h2>
    </div>

    <div class="mt-10 sm:mx-auto sm:w-full sm:max-w-sm">
      <form class="space-y-6" @submit.prevent="handleSignup">
        <div>
          <label for="username" class="block text-sm font-medium text-gray-900">Username</label>
          <input id="username" v-model.trim="username" name="username" type="text" required autocomplete="username" class="input mt-2" />
        </div>
        <div>
          <label for="password" class="block text-sm font-medium text-gray-900">Password</label>
          <input id="password" v-model.trim="password" name="password" type="password" required autocomplete="new-password" class="input mt-2" />
        </div>
        <UiButton type="submit" variant="primary" class="w-full" :pending="pending">Sign up</UiButton>
      </form>

      <p class="mt-10 text-center text-sm text-gray-500">
        Already have an account?
        <router-link to="/login" class="font-semibold text-primary-700 hover:text-primary-800">Log in</router-link>
      </p>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue';
import { useRouter } from 'vue-router';
import { signup, logout } from '../auth';
import UiButton from '../components/ui/UiButton.vue';
import { toast } from '../composables/toast.js';

const router = useRouter();
const username = ref('');
const password = ref('');
const pending = ref(false);

const handleSignup = async () => {
  pending.value = true;
  const result = await signup(username.value, password.value);
  pending.value = false;
  if (result.success) {
    await logout(); // signing up logs in: log out, and let the user log in on purpose
    toast.success('Account created. You can log in now.');
    router.push('/login');
  } else {
    toast.error(result.message);
  }
};
</script>
