<template>
  <div class="bg-teal-700">
    <!-- Navbar -->
    <header class="absolute inset-x-0 top-0 z-50">
      <nav class="flex items-center justify-between p-6 lg:px-8" aria-label="Global">
        <div class="flex lg:flex-1">
          <router-link to="/" class="-m-1.5 p-1.5">
            <span class="sr-only">Blab</span>
            <img class="h-8 w-auto" src="https://tailwindui.com/img/logos/mark.svg?color=white" alt="Blab Logo" />
          </router-link>
        </div>
        <div class="flex lg:hidden">
          <button type="button" class="-m-2.5 inline-flex items-center justify-center rounded-md p-2.5 text-gray-100" @click="mobileMenuOpen = true">
            <span class="sr-only">Open main menu</span>
            <Bars3Icon class="h-6 w-6" aria-hidden="true" />
          </button>
        </div>
        <div class="hidden lg:flex lg:gap-x-12">
          <router-link v-for="item in navigation" :key="item.name" :to="item.href" class="text-sm font-semibold leading-6 text-white">{{ item.name }}</router-link>
        </div>
        <div v-if="!isLoggedIn" class="hidden lg:flex lg:flex-1 lg:justify-end">
          <router-link to="/login" class="text-sm font-semibold leading-6 text-white">Log in <span aria-hidden="true">&rarr;</span></router-link>
        </div>
        <div v-else class="hidden lg:flex lg:flex-1 lg:justify-end">
          <button @click="handeLogout" class="text-sm font-semibold leading-6 text-white">Logout</button>
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
              <router-link v-for="item in navigation" :key="item.name" :to="item.href" class="-mx-3 block rounded-lg px-3 py-2 text-base font-semibold leading-7 text-gray-900 hover:bg-gray-50">{{ item.name }}</router-link>
            </div>
            <div v-if="!isLoggedIn" class="py-6">
              <router-link to="/login" class="-mx-3 block rounded-lg px-3 py-2.5 text-base font-semibold leading-7 text-gray-900 hover:bg-gray-50">Log in</router-link>
            </div>
            <div v-else class="py-6">
              <button @click="handeLogout" class="-mx-3 block rounded-lg px-3 py-2.5 text-base font-semibold leading-7 text-gray-900 hover:bg-gray-50">Logout</button>
            </div>
          </div>
        </DialogPanel>
      </Dialog>
    </header>

    <!-- Hero Section -->
    <div class="relative isolate px-6 pt-14 lg:px-8">
      <div class="absolute inset-x-0 -top-40 -z-10 transform-gpu overflow-hidden blur-3xl sm:-top-80" aria-hidden="true">
        <div class="relative left-[calc(50%-11rem)] aspect-[1155/678] w-[36.125rem] -translate-x-1/2 rotate-[30deg] bg-gradient-to-tr from-[#ff80b5] to-[#9089fc] opacity-30 sm:left-[calc(50%-30rem)] sm:w-[72.1875rem]" style="clip-path: polygon(74.1% 44.1%, 100% 61.6%, 97.5% 26.9%, 85.5% 0.1%, 80.7% 2%, 72.5% 32.5%, 60.2% 62.4%, 52.4% 68.1%, 47.5% 58.3%, 45.2% 34.5%, 27.5% 76.7%, 0.1% 64.9%, 17.9% 100%, 27.6% 76.8%, 76.1% 97.7%, 74.1% 44.1%)" />
      </div>
      <div class="mx-auto max-w-2xl py-32 sm:py-48 lg:py-56">
        <div class="text-center">
          <h1 class="text-4xl font-bold tracking-tight text-white sm:text-6xl">Remote Reservation and Topology Visualization</h1>
          <p class="mt-6 text-lg leading-8 text-gray-200">Blab offers remote reservation of switches and interactive visualization of network topology.</p>
          <div class="mt-10 flex items-center justify-center gap-x-6">
            <router-link to="/reservation" class="rounded-md bg-indigo-600 px-3.5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600">Get Started</router-link>
            <a href="/api" class="text-sm font-semibold leading-6 text-white">API Access <span aria-hidden="true">→</span></a>
          </div>
        </div>
      </div>
      <div class="absolute inset-x-0 top-[calc(100%-13rem)] -z-10 transform-gpu overflow-hidden blur-3xl sm:top-[calc(100%-30rem)]" aria-hidden="true">
        <div class="relative left-[calc(50%+3rem)] aspect-[1155/678] w-[36.125rem] -translate-x-1/2 bg-gradient-to-tr from-[#ff80b5] to-[#9089fc] opacity-30 sm:left-[calc(50%+36rem)] sm:w-[72.1875rem]" style="clip-path: polygon(74.1% 44.1%, 100% 61.6%, 97.5% 26.9%, 85.5% 0.1%, 80.7% 2%, 72.5% 32.5%, 60.2% 62.4%, 52.4% 68.1%, 47.5% 58.3%, 45.2% 34.5%, 27.5% 76.7%, 0.1% 64.9%, 17.9% 100%, 27.6% 76.8%, 76.1% 97.7%, 74.1% 44.1%)" />
      </div>
    </div>

    <!-- Feature Section -->
    <div class="bg-white py-24 sm:py-32">
      <div class="mx-auto max-w-7xl px-6 lg:px-8">
        <div class="mx-auto max-w-2xl lg:text-center">
          <h2 class="text-base font-semibold leading-7 text-indigo-600">Blab Features</h2>
          <p class="mt-2 text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">Everything you need to manage your network</p>
          <p class="mt-6 text-lg leading-8 text-gray-600">Blab provides various features to help you deploy and manage your network infrastructure efficiently.</p>
        </div>
        <div class="mx-auto mt-16 max-w-2xl sm:mt-20 lg:mt-24 lg:max-w-4xl">
          <dl class="grid max-w-xl grid-cols-1 gap-x-8 gap-y-10 lg:max-w-none lg:grid-cols-2 lg:gap-y-16">
            <div v-for="feature in features" :key="feature.name" class="relative pl-16">
              <dt class="text-base font-semibold leading-7 text-gray-900">
                <div class="absolute left-0 top-0 flex h-10 w-10 items-center justify-center rounded-lg bg-indigo-600">
                  <component :is="feature.icon" class="h-6 w-6 text-white" aria-hidden="true" />
                </div>
                {{ feature.name }}
              </dt>
              <dd class="mt-2 text-base leading-7 text-gray-600">{{ feature.description }}</dd>
            </div>
          </dl>
        </div>
      </div>
    </div>

    <!-- Stats Section -->
    <div class="bg-white py-24 sm:py-32">
      <div class="mx-auto max-w-7xl px-6 lg:px-8">
        <dl class="grid grid-cols-1 gap-x-8 gap-y-16 text-center lg:grid-cols-3">
          <div v-for="stat in stats" :key="stat.id" class="mx-auto flex max-w-xs flex-col gap-y-4">
            <dt class="text-base leading-7 text-gray-600">{{ stat.name }}</dt>
            <dd class="order-first text-3xl font-semibold tracking-tight text-gray-900 sm:text-5xl">{{ stat.value }}</dd>
          </div>
        </dl>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Dialog, DialogPanel } from '@headlessui/vue'
import { Bars3Icon, XMarkIcon, CloudArrowUpIcon, LockClosedIcon, ServerIcon } from '@heroicons/vue/24/outline'
import { isAuthenticated, logout } from '../auth'; // Import your authentication functions

const router = useRouter()

const navigation = [
  { name: 'Reservation', href: '/reservation' },
  { name: 'Topology', href: '/topology' }
]

const mobileMenuOpen = ref(false)

const isLoggedIn = ref(isAuthenticated()); // Assuming initially user is not logged in


const features = [
  {
    name: 'Remote Reservation',
    description:
      'Reserve switches remotely for testing and development purposes, optimizing network utilization.',
    icon: CloudArrowUpIcon,
  },
  {
    name: 'Topology Visualization',
    description:
      'Visualize network topologies dynamically to understand and manage complex network infrastructures.',
    icon: LockClosedIcon,
  },
  {
    name: 'Efficient Workflow',
    description:
      'Streamline your network management workflow with intuitive tools and features designed for efficiency.',
    icon: ServerIcon,
  },
]

const stats = ref([])

onMounted(async () => {
  // No need for API call anymore
  stats.value = {
    'Number of Users' : 2,
    'Ongoing Reservation' : 1,
    'Switch connected to Blab' : 2
  };
})

// Logout function
const handeLogout = () => {
  // Call your logout function from auth module
  logout();
  // Update isLoggedIn state
  isLoggedIn.value = false;
}
</script>