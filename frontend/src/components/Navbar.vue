<template>
  <header :class="[$route.path === '/' ? 'absolute' : 'relative', 'inset-x-0', 'top-0', 'z-50', {'bg-teal-700': $route.path !== '/'}]">
      <nav class="flex items-center justify-between p-6 lg:px-8" aria-label="Global">
        <div class="flex lg:flex-1">
          <router-link to="/" class="-m-1.5 p-1.5">
            <span class="sr-only">Blab</span>
            <img class="h-12 w-auto" src="/logo.png" alt="Blab Logo" />
          </router-link>
        </div>
        <div class="flex lg:hidden">
          <button type="button" class="-m-2.5 inline-flex items-center justify-center rounded-md p-2.5 text-gray-100" @click="mobileMenuOpen = true">
            <span class="sr-only">Open main menu</span>
            <Bars3Icon class="h-6 w-6" aria-hidden="true" />
          </button>
        </div>
        <div class="hidden lg:flex lg:gap-x-12">
          <router-link
            v-for="item in navigation"
            :key="item.name"
            :to="item.href"
            class="text-sm font-semibold leading-6 text-white py-2 px-4 rounded transition duration-300"
            :class="{'font-bold': $route.path === item.href, 'hover:text-teal-300': $route.path !== item.href}"
          >
            {{ item.name }}
          </router-link>
        </div>
        <div v-if="!isLoggedIn" class="hidden lg:flex lg:flex-1 lg:justify-end">
          <router-link to="/login" class="text-sm font-semibold leading-6 text-white">Log in <span aria-hidden="true">&rarr;</span></router-link>
        </div>
        <div v-else class="hidden lg:flex lg:flex-1 lg:justify-end">
          <button @click="showLogoutDialog = true" class="text-sm font-semibold leading-6 text-white">Logout</button>
        </div>
      </nav>
      <Dialog as="div" class="lg:hidden" @close="mobileMenuOpen = false" :open="mobileMenuOpen">
        <div class="fixed inset-0 z-50" />
        <DialogPanel class="fixed inset-y-0 right-0 z-50 w-full overflow-y-auto bg-white px-6 py-6 sm:max-w-sm sm:ring-1 sm:ring-gray-900/10">
          <div class="flex items-center justify-between">
            <router-link to="/" class="-m-1.5 p-1.5">
              <span class="sr-only">Blab</span>
              <img class="h-8 w-auto" src="https://tailwindui.com/img/logos/mark.svg?color=white" alt="Blab Logo" />
            </router-link>
            <button type="button" class="-m-2.5 rounded-md p-2.5 text-gray-700" @click="mobileMenuOpen = false">
              <span class="sr-only">Close menu</span>
              <XMarkIcon class="h-6 w-6" aria-hidden="true" />
            </button>
          </div>
          <div class="mt-6 flow-root">
            <div class="-my-6 divide-y divide-gray-500/10">
              <router-link
              v-for="item in navigation"
              :key="item.name"
              :to="item.href"
              class="text-sm font-semibold leading-6 text-white py-2 px-4 rounded transition duration-300"
              :class="{'font-bold': $route.path === item.href, 'hover:text-teal-300': $route.path !== item.href}"
            >
              {{ item.name }}
            </router-link>            
          </div>
            <div v-if="!isLoggedIn" class="py-6">
              <router-link to="/login" class="-mx-3 block rounded-lg px-3 py-2.5 text-base font-semibold leading-7 text-gray-900 hover:bg-gray-50">Log in</router-link>
            </div>
            <div v-else class="py-6">
              <button @click="showLogoutDialog = true" class="-mx-3 block rounded-lg px-3 py-2.5 text-base font-semibold leading-7 text-gray-900 hover:bg-gray-50">Logout</button>
            </div>
          </div>
        </DialogPanel>
      </Dialog>
      <Dialog v-if="showLogoutDialog" @close="showLogoutDialog = false" :open="showLogoutDialog">
        <DialogPanel class="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50">
          <div class="bg-white p-6 rounded-lg shadow-lg">
            <h2 class="text-lg font-semibold mb-4">Confirm Logout</h2>
            <p class="mb-4">Are you sure you want to logout?</p>
            <div class="flex justify-end">
              <button @click="showLogoutDialog = false" class="mr-4 px-4 py-2 bg-gray-300 rounded">Cancel</button>
              <button @click="confirmLogout" class="px-4 py-2 bg-red-600 text-white rounded">Logout</button>
            </div>
          </div>
        </DialogPanel>
      </Dialog>
    </header>
</template>

<script setup>
import { ref } from 'vue';
import { useRouter } from 'vue-router'
import { Dialog, DialogPanel } from '@headlessui/vue'
import { Bars3Icon, XMarkIcon} from '@heroicons/vue/24/outline'
import { logout, isAuthenticated } from '../auth';

const mobileMenuOpen = ref(false)
const showLogoutDialog = ref(false)

const router = useRouter();
const isLoggedIn = ref(isAuthenticated());

const confirmLogout = async () => {
    await logout();  // This now always clears localStorage
    isLoggedIn.value = false;
    showLogoutDialog.value = false;
    router.push('/');
};

const navigation = [
  { name: 'Reservation', href: '/reservation' },
  { name: 'Topology', href: '/topology' }
];
</script>