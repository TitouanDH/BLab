<template>
  <!-- Switch accounts (see CONTEXT.md): the caller's own, on the Switches they may work on -->
  <section id="switch-accounts" class="rounded-lg bg-white p-4 shadow-sm ring-1 ring-gray-200" aria-label="Your Switch accounts">
    <h2 class="text-lg font-semibold text-gray-900">Your Switch accounts</h2>
    <p class="mb-3 text-sm text-gray-600">
      Log in to your Switches by SSH with the login and the password shown here: the login is your
      BLab name when the Switch accepts it as is.
      BLab creates these Switch accounts when you reserve a Switch or a Topology is shared with
      you, and removes them at Release. The <code>admin</code> login is BLab's only.
    </p>
    <p v-if="loadError" class="text-sm text-warning-700">{{ loadError }}</p>
    <p v-else-if="loaded && accounts.length === 0" class="text-sm text-gray-500">
      None: you hold no Switch, and no Topology shared with you holds one.
    </p>
    <div v-else-if="accounts.length" class="overflow-x-auto">
      <table class="min-w-full text-left text-sm">
        <thead class="text-gray-500">
          <tr>
            <th class="py-1 pr-4 font-medium">Switch</th>
            <th class="py-1 pr-4 font-medium">Holder</th>
            <th class="py-1 pr-4 font-medium">Login</th>
            <th class="py-1 font-medium">Password</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="account in accounts" :key="account.switch" :data-switch-login="account.mngt_IP" class="border-t align-middle">
            <td class="py-1.5 pr-4 font-mono">{{ account.mngt_IP }}</td>
            <td class="py-1.5 pr-4">{{ account.holder }}</td>
            <td class="py-1.5 pr-4 font-mono">{{ account.name }}</td>
            <td class="py-1.5">
              <div v-if="account.state === 'ready'" class="flex flex-wrap items-center gap-2">
                <span class="font-mono" data-password>{{ shown[account.switch] ? account.password : '••••••••••••' }}</span>
                <UiButton size="sm" @click="toggle(account.switch)">{{ shown[account.switch] ? 'Hide' : 'Show' }}</UiButton>
                <UiButton size="sm" @click="copy(account)">{{ copied === account.switch ? 'Copied' : 'Copy' }}</UiButton>
              </div>
              <span v-else-if="account.state === 'pending'" class="text-gray-500">Being created...</span>
              <span v-else class="text-warning-700">Not created yet, BLab keeps trying: {{ account.error }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue';
import UiButton from './ui/UiButton.vue';
import { switchAccountService } from '../utils/apiService.js';

const accounts = ref([]);
const loaded = ref(false);
const loadError = ref('');
const shown = ref({});
const copied = ref(null);
const RETRY_MS = 10 * 1000;
let timer = null;

const refresh = async () => {
  const result = await switchAccountService.getMine();
  if (result.success) {
    accounts.value = result.data.switch_accounts || [];
    loadError.value = '';
  } else {
    loadError.value = `Couldn't load your Switch accounts. ${result.message}`;
  }
  loaded.value = true;
  // BLab retries the Switch accounts it couldn't create: look again until they are ready
  clearTimeout(timer);
  if (accounts.value.some(a => a.state !== 'ready')) timer = setTimeout(refresh, RETRY_MS);
};

const toggle = (switchId) => {
  shown.value = { ...shown.value, [switchId]: !shown.value[switchId] };
};

const copy = async (account) => {
  try {
    await navigator.clipboard.writeText(account.password);
    copied.value = account.switch;
    setTimeout(() => { if (copied.value === account.switch) copied.value = null; }, 2000);
  } catch {
    // No clipboard here (plain HTTP, old browser): show it to copy by hand
    shown.value = { ...shown.value, [account.switch]: true };
  }
};

onMounted(refresh);
onUnmounted(() => clearTimeout(timer));

defineExpose({ refresh });
</script>
