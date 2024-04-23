<template>
  <nav class="bg-teal-700 p-4">
    <div class="container mx-auto flex items-center justify-between">
      <router-link to="/" class="text-white text-xl font-semibold">BLab</router-link>
      <button @click="toggleMenu" class="block lg:hidden text-white focus:outline-none">
        <svg class="h-6 w-6 fill-current" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg">
          <title>Menu</title>
          <path d="M0 3h20v2H0V3zm0 6h20v2H0V9zm0 6h20v2H0v-2z"></path>
        </svg>
      </button>
      <div :class="{ 'hidden': !menuOpen }" class="lg:flex lg:items-center lg:w-auto">
        <div class="text-sm lg:flex-grow lg:flex lg:justify-start">
          <router-link v-if="isLoggedIn" to="/reservation" class="block mt-4 lg:inline-block lg:mt-0 text-white hover:text-gray-200 mr-4">
            Reservation
          </router-link>
          <router-link v-if="isLoggedIn" to="/topology" class="block mt-4 lg:inline-block lg:mt-0 text-white hover:text-gray-200 mr-4">
            Topology
          </router-link>
        </div>
        <div>
          <button v-if="isLoggedIn" @click="handleLogout" class="text-white font-semibold py-2 px-4 rounded-full hover:bg-blue-200 hover:text-blue-800 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 mt-5 lg:mt-0">Logout</button>
          <router-link v-else to="/login" class="text-white font-semibold  px-4 rounded-full hover:bg-blue-200 hover:text-blue-800 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 mt-5 lg:mt-0 py-4">Login</router-link>
        </div>
      </div>
    </div>
  </nav>
</template>

<script>
import { ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { logout, isAuthenticated } from '../auth';

export default {
  setup() {
    const router = useRouter();
    const isLoggedIn = ref(isAuthenticated());

    const handleLogout = () => {
      logout();
      isLoggedIn.value = false; // Update isLoggedIn after logout
      router.push('/');
    };

    return { isLoggedIn, handleLogout };
  }
}
</script>