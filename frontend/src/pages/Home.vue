<template>
  <div>
    <!-- Hero Section -->
    <div class="bg-primary-700 px-6 lg:px-8">
      <div class="mx-auto max-w-2xl py-24 sm:py-32">
        <div class="text-center">
          <h1 class="text-4xl font-bold tracking-tight text-white sm:text-6xl">Remote Reservation and Topology Visualization</h1>
          <p class="mt-6 text-lg leading-8 text-primary-50">BLab lets you reserve lab Switches and wire them together through the backbone, without touching cables.</p>
          <div class="mt-10 flex items-center justify-center gap-x-6">
            <UiButton to="/reservation">Get started</UiButton>
            <a href="/api" class="text-sm font-semibold leading-6 text-white">API access <span aria-hidden="true">→</span></a>
          </div>
        </div>
      </div>
    </div>

    <!-- Feature Section -->
    <div class="bg-white py-24 sm:py-32">
      <div class="mx-auto max-w-7xl px-6 lg:px-8">
        <div class="mx-auto max-w-2xl lg:text-center">
          <h2 class="text-base font-semibold leading-7 text-primary-700">BLab features</h2>
          <p class="mt-2 text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">Everything you need to manage your network</p>
          <p class="mt-6 text-lg leading-8 text-gray-600">BLab provides various features to help you deploy and manage your network infrastructure efficiently.</p>
        </div>
        <div class="mx-auto mt-16 max-w-2xl sm:mt-20 lg:mt-24 lg:max-w-4xl">
          <dl class="grid max-w-xl grid-cols-1 gap-x-8 gap-y-10 lg:max-w-none lg:grid-cols-2 lg:gap-y-16">
            <div v-for="feature in features" :key="feature.name" class="relative pl-16">
              <dt class="text-base font-semibold leading-7 text-gray-900">
                <div class="absolute left-0 top-0 flex h-10 w-10 items-center justify-center rounded-lg bg-primary-600">
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
import { CloudArrowUpIcon, LockClosedIcon, ServerIcon } from '@heroicons/vue/24/outline'
import UiButton from '../components/ui/UiButton.vue'

const features = [
  {
    name: 'Remote Reservation',
    description:
      'Reserve Switches remotely for testing and development purposes, optimizing network utilization.',
    icon: CloudArrowUpIcon,
  },
  {
    name: 'Topology',
    description:
      'See your Switches and the Links between them, and connect ports without touching a cable.',
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
  // Hard-coded stats - consider adding API endpoint for dynamic stats
  stats.value = [
    { id: 1, name: 'Users', value: '2' },
    { id: 2, name: 'Ongoing Reservations', value: '1' },
    { id: 3, name: 'Switches connected to BLab', value: '2' }
  ];
})
</script>