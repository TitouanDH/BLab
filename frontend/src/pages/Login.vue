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
              <input v-model.trim="username" id="username" name="username" type="text" required="" class="block w-full rounded-md border-0 py-1.5 px-1.5 text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 placeholder:text-gray-400 focus:ring-2 focus:ring-inset focus:ring-indigo-600 sm:text-sm sm:leading-6" />
            </div>
          </div>

          <div>
            <label for="password" class="block text-sm font-medium leading-6 text-gray-900">Password</label>
            <div class="mt-2">
              <input v-model.trim="password" id="password" name="password" type="password" required="" class="block w-full rounded-md border-0 py-1.5 px-1.5 text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 placeholder:text-gray-400 focus:ring-2 focus:ring-inset focus:ring-indigo-600 sm:text-sm sm:leading-6" />
            </div>
          </div>

          <div>
            <button type="submit" class="flex w-full justify-center rounded-md bg-teal-700 px-3 py-1.5 text-sm font-semibold leading-6 text-white shadow-sm hover:bg-teal-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600">Sign in</button>
          </div>
        </form>

        <!-- Error message for login failure -->
        <div v-if="errorMessage" class="bg-red-100 border border-red-400 text-red-700 px-4 py-3 mt-2 rounded relative" role="alert">
          <strong class="font-bold">Oops! </strong>
          <span class="block sm:inline">{{ errorMessage }}</span>
          <span class="absolute top-0 bottom-0 right-0 px-4 py-3">
            <svg @click="clearError" class="fill-current h-6 w-6 text-red-500 cursor-pointer" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><title>Close</title><path d="M14.348 14.849a1.2 1.2 0 0 1-1.697 0L10 11.819l-2.651 3.029a1.2 1.2 0 1 1-1.697-1.697l2.758-3.15-2.759-3.152a1.2 1.2 0 1 1 1.697-1.697L10 8.183l2.651-3.031a1.2 1.2 0 1 1 1.697 1.697l-2.758 3.152 2.758 3.15a1.2 1.2 0 0 1 0 1.698z"/></svg>
          </span>
        </div>
        
        <p class="mt-10 text-center text-sm text-gray-500">
          Not a member?
          {{ ' ' }}
          <router-link to="/signup" class="font-semibold leading-6 text-teal-700 hover:text-teal-700">Sign Up !</router-link>
        </p>
      </div>
    </div>
  </div>
</template>

<script>
import { ref } from 'vue';
import Navbar from '../components/Navbar.vue';
import { useRouter } from 'vue-router';
import { login } from '../auth'; // Assuming you have implemented the login function in auth.js

export default {
  components: {
    Navbar
  },
  setup() {
    const router = useRouter();
    let username = ref('');
    let password = ref('');
    const errorMessage = ref(null); // Error message state

    const handleLogin = async () => {
      try {
        const success = await login(username.value, password.value);
        if (success) {
          // Redirect to dashboard or desired page
          router.push('/');
        } else {
          // Set error message for failed login
          errorMessage.value = "Incorrect username or password.";
          username = "";
          password = "";
        }
      } catch (error) {
        console.error(error);
        // Set error message for other errors
        errorMessage.value = "An error occurred. Please try again later.";
        username = "";
        password = "";
      }
    };

    const clearError = () => {
      // Clear error message
      errorMessage.value = null;
    };

    return { username, password, handleLogin, errorMessage, clearError };
  }
}
</script>