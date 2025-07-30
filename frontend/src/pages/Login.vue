<template>
  <div>
    <Navbar />
    <div class="flex min-h-full flex-1 flex-col justify-center px-6 py-12 lg:px-8">
      <div class="sm:mx-auto sm:w-full sm:max-w-sm">
        <h2 class="mt-10 text-center text-2xl font-bold leading-9 tracking-tight text-gray-900">Sign in to your account</h2>
      </div>

      <div class="mt-10 sm:mx-auto sm:w-full sm:max-w-sm">
        <form @submit.prevent="handleLogin" class="space-y-6">
          <div>
            <label for="username" class="block text-sm font-medium leading-6 text-gray-900">Username</label>
            <div class="mt-2">
              <input v-model.trim="username" id="username" name="username" type="text" required="" autocomplete="username" class="block w-full rounded-md border-0 py-1.5 px-1.5 text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 placeholder:text-gray-400 focus:ring-2 focus:ring-inset focus:ring-teal-600 sm:text-sm sm:leading-6" />
            </div>
          </div>

          <div>
            <label for="password" class="block text-sm font-medium leading-6 text-gray-900">Password</label>
            <div class="mt-2">
              <input v-model.trim="password" id="password" name="password" type="password" required="" autocomplete="current-password" class="block w-full rounded-md border-0 py-1.5 px-1.5 text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 placeholder:text-gray-400 focus:ring-2 focus:ring-inset focus:ring-teal-600 sm:text-sm sm:leading-6" />
            </div>
          </div>

          <div>
            <button type="submit" class="flex w-full justify-center rounded-md bg-teal-700 px-3 py-1.5 text-sm font-semibold leading-6 text-white shadow-sm hover:bg-teal-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-600">Sign in</button>
          </div>
        </form>

        <!-- Error message for login failure -->
        <AlertDialog v-if="errorMessage" :message="errorMessage" @close="clearError" />
        
        <p class="mt-10 text-center text-sm text-gray-500">
          Not a member?
          {{ ' ' }}
          <router-link to="/signup" class="font-semibold leading-6 text-teal-700 hover:text-teal-700">Sign Up !</router-link>
        </p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue';
import { useRouter } from 'vue-router';
import { login } from '../auth'; // adjust the path according to your project structure
import Navbar from '../components/Navbar.vue';
import AlertDialog from '../components/AlertDialog.vue';

let username = ref('');
let password = ref('');
let errorMessage = ref(null);
const router = useRouter();

const handleLogin = async () => {
  try {
    const result = await login(username.value, password.value);
    if (result.success) {
      // Redirect to dashboard or desired page
      router.push('/');
    } else {
      // Set error message for failed login
      errorMessage.value = result.message || "Login failed. Please try again.";
      username.value = "";
      password.value = "";
    }
  } catch (error) {
    console.error(error);
    // Set error message for other errors
    errorMessage.value = "An unexpected error occurred. Please try again later.";
    username.value = "";
    password.value = "";
  }
};

const clearError = () => {
  // Clear error message
  errorMessage.value = null;
};
</script>