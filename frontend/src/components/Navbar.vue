<template>
  <header class="relative z-40 bg-primary-700">
    <nav class="mx-auto flex items-center justify-between px-4 py-3 lg:px-8" aria-label="Global">
      <div class="flex lg:flex-1">
        <router-link to="/" class="-m-1.5 p-1.5">
          <span class="sr-only">BLab</span>
          <img class="h-9 w-auto" src="/logo.png" alt="BLab logo" />
        </router-link>
      </div>
      <div class="flex lg:hidden">
        <button type="button" class="-m-2.5 inline-flex items-center justify-center rounded-md p-2.5 text-white" @click="mobileMenuOpen = true">
          <span class="sr-only">Open main menu</span>
          <Bars3Icon class="h-6 w-6" aria-hidden="true" />
        </button>
      </div>
      <div class="hidden lg:flex lg:gap-x-2">
        <router-link
          v-for="item in navigation"
          :key="item.name"
          :to="item.href"
          class="rounded-md px-4 py-2 text-sm font-semibold text-white transition-colors"
          :class="isActive(item) ? 'bg-primary-800' : 'hover:bg-primary-600'"
        >
          {{ item.name }}
        </router-link>
      </div>
      <div class="hidden lg:flex lg:flex-1 lg:justify-end">
        <router-link v-if="!session.loggedIn" to="/login" class="text-sm font-semibold text-white">Log in <span aria-hidden="true">&rarr;</span></router-link>
        <button v-else type="button" class="text-sm font-semibold text-white hover:text-primary-100" @click="doLogout">Log out</button>
      </div>
    </nav>

    <!-- Phone menu: dark text on a white panel -->
    <Dialog as="div" class="lg:hidden" :open="mobileMenuOpen" @close="mobileMenuOpen = false">
      <div class="fixed inset-0 z-50 bg-black/20" />
      <DialogPanel class="fixed inset-y-0 right-0 z-50 w-full overflow-y-auto bg-white px-6 py-4 sm:max-w-sm sm:ring-1 sm:ring-gray-900/10">
        <div class="flex items-center justify-between">
          <router-link to="/" class="-m-1.5 rounded-md bg-primary-700 p-1.5" @click="mobileMenuOpen = false">
            <span class="sr-only">BLab</span>
            <img class="h-8 w-auto" src="/logo.png" alt="BLab logo" />
          </router-link>
          <button type="button" class="-m-2.5 rounded-md p-2.5 text-gray-700" @click="mobileMenuOpen = false">
            <span class="sr-only">Close menu</span>
            <XMarkIcon class="h-6 w-6" aria-hidden="true" />
          </button>
        </div>
        <div class="mt-6 flow-root">
          <div class="-my-6 divide-y divide-gray-500/10">
            <div class="space-y-1 py-6">
              <router-link
                v-for="item in navigation"
                :key="item.name"
                :to="item.href"
                class="-mx-3 block rounded-lg px-3 py-2 text-base font-semibold text-gray-900 hover:bg-gray-50"
                :class="{ 'bg-primary-50 text-primary-800': isActive(item) }"
                @click="mobileMenuOpen = false"
              >
                {{ item.name }}
              </router-link>
            </div>
            <div class="py-6">
              <router-link v-if="!session.loggedIn" to="/login" class="-mx-3 block rounded-lg px-3 py-2.5 text-base font-semibold text-gray-900 hover:bg-gray-50" @click="mobileMenuOpen = false">Log in</router-link>
              <button v-else type="button" class="-mx-3 block w-full rounded-lg px-3 py-2.5 text-left text-base font-semibold text-gray-900 hover:bg-gray-50" @click="doLogout">Log out</button>
            </div>
          </div>
        </div>
      </DialogPanel>
    </Dialog>
  </header>
</template>

<script setup>
import { ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Dialog, DialogPanel } from '@headlessui/vue';
import { Bars3Icon, XMarkIcon } from '@heroicons/vue/24/outline';
import { logout, session } from '../auth';

const mobileMenuOpen = ref(false);
const route = useRoute();
const router = useRouter();


const navigation = [
  { name: 'Reservation', href: '/reservation' },
  { name: 'Topology', href: '/topology' },
  { name: 'Lab status', href: '/status' },
];
const isActive = (item) => route.path === item.href;

// Logging out loses nothing, so it asks for no confirmation
const doLogout = async () => {
  mobileMenuOpen.value = false;
  await logout();
  router.push('/');
};
</script>
