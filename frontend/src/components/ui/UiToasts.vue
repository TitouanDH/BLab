<template>
  <!-- Where toasts (composables/toast.js) appear: bottom right, newest last. -->
  <div class="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-full max-w-sm flex-col gap-2 px-4 sm:px-0">
    <TransitionGroup
      enter-from-class="translate-y-2 opacity-0"
      enter-active-class="transition duration-200"
      leave-to-class="opacity-0"
      leave-active-class="transition duration-150"
    >
      <div
        v-for="t in toasts"
        :key="t.id"
        :role="t.kind === 'error' ? 'alert' : 'status'"
        :data-kind="t.kind"
        :class="[
          'pointer-events-auto flex items-start gap-3 rounded-lg shadow-lg ring-1',
          t.kind === 'quiet' ? 'bg-gray-800 px-3 py-2 text-xs text-gray-100 ring-black/10' : 'bg-white p-4 text-sm text-gray-800 ring-black/5',
        ]"
      >
        <CheckCircleIcon v-if="t.kind === 'success'" class="h-5 w-5 flex-none text-success-600" aria-hidden="true" />
        <ExclamationTriangleIcon v-else-if="t.kind === 'error'" class="h-5 w-5 flex-none text-warning-600" aria-hidden="true" />
        <SignalSlashIcon v-else class="h-4 w-4 flex-none text-gray-300" aria-hidden="true" />
        <p class="flex-1 break-words">{{ t.message }}</p>
        <button
          type="button"
          class="flex-none rounded text-gray-400 hover:text-gray-600"
          aria-label="Close"
          @click="dismiss(t.id)"
        >
          <XMarkIcon :class="t.kind === 'quiet' ? 'h-4 w-4' : 'h-5 w-5'" />
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

<script setup>
import { CheckCircleIcon, ExclamationTriangleIcon, SignalSlashIcon, XMarkIcon } from '@heroicons/vue/24/outline';
import { dismiss, toasts } from '../../composables/toast.js';
</script>
