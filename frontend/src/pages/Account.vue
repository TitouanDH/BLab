<template>
  <div class="flex flex-1 flex-col justify-center px-6 py-12 lg:px-8">
    <div class="sm:mx-auto sm:w-full sm:max-w-sm">
      <h2 class="text-center text-2xl font-bold tracking-tight text-gray-900">Your account</h2>
      <p v-if="step === 'email'" class="mt-4 text-center text-sm text-gray-700">
        BLab needs your email address before you go on: your Rainbow login, so that BLab can message you there.
      </p>
      <p v-else-if="step === 'switch_password'" class="mt-4 text-center text-sm text-gray-700">
        Choose your Switch password before you go on: you log in to every Switch you work on with it.
      </p>
    </div>

    <div class="mt-10 space-y-10 sm:mx-auto sm:w-full sm:max-w-sm">
      <UiSpinner v-if="loading" class="mx-auto" />
      <template v-else>
        <form class="space-y-6" @submit.prevent="save">
          <div>
            <span class="block text-sm font-medium text-gray-900">Username</span>
            <p class="mt-2 text-sm text-gray-700">{{ account.username }}</p>
          </div>
          <div>
            <label for="email" class="block text-sm font-medium text-gray-900">Email</label>
            <input id="email" v-model.trim="email" name="email" type="email" required autocomplete="email" class="input mt-2" aria-describedby="email-help" />
            <p id="email-help" class="mt-1 text-xs text-gray-500">Your Rainbow login: BLab messages you there.</p>
          </div>
          <UiButton type="submit" variant="primary" class="w-full" :pending="pending">Save</UiButton>
        </form>

        <!-- The Switch password (CONTEXT.md): chosen once, changed here, never shown again -->
        <form
          v-if="account.switch_password_enabled && step !== 'email'"
          class="space-y-4 border-t pt-8"
          aria-label="Switch password"
          @submit.prevent="saveSwitch"
        >
          <div>
            <h3 class="text-base font-semibold text-gray-900">
              {{ account.switch_password_set ? 'Change your Switch password' : 'Choose your Switch password' }}
            </h3>
            <p class="mt-1 text-sm text-gray-600">
              You log in by SSH to every Switch you may work on as <code class="font-mono">{{ account.switch_login }}</code>
              with this password. BLab sets it on all your Switch accounts and never shows it again.
              Don't reuse a password from elsewhere.
            </p>
          </div>
          <div>
            <label for="switch-password" class="block text-sm font-medium text-gray-900">New Switch password</label>
            <input
              id="switch-password" v-model="switchPassword" type="password" autocomplete="new-password"
              class="input mt-2" aria-describedby="switch-password-rules"
            />
          </div>
          <ul id="switch-password-rules" class="grid grid-cols-1 gap-1 text-xs sm:grid-cols-2" aria-label="Switch password rules">
            <li
              v-for="rule in rules" :key="rule.id" :data-rule="rule.id" :data-passed="rule.passed"
              :class="rule.passed ? 'text-success-700' : 'text-gray-500'" class="flex items-center gap-1"
            >
              <CheckIcon v-if="rule.passed" class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <XMarkIcon v-else class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span>{{ rule.label }}<span class="sr-only">{{ rule.passed ? ': yes' : ': not yet' }}</span></span>
            </li>
          </ul>
          <div>
            <label for="switch-password-again" class="block text-sm font-medium text-gray-900">Type it again</label>
            <input id="switch-password-again" v-model="switchPasswordAgain" type="password" autocomplete="new-password" class="input mt-2" />
            <p v-if="switchPasswordAgain && !same" class="mt-1 text-xs text-warning-700">The two don't match.</p>
          </div>
          <ul v-if="refused.length" role="alert" class="list-inside list-disc text-sm text-warning-700">
            <li v-for="why in refused" :key="why">{{ why }}</li>
          </ul>
          <UiButton type="submit" variant="primary" class="w-full" :pending="switchPending" :disabled="!acceptable">
            Save Switch password
          </UiButton>
        </form>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { CheckIcon, XMarkIcon } from '@heroicons/vue/20/solid';
import { getAccount, onboardingStep, saveEmail, saveSwitchPassword } from '../auth';
import UiButton from '../components/ui/UiButton.vue';
import UiSpinner from '../components/ui/UiSpinner.vue';
import { toast } from '../composables/toast.js';
import { checkSwitchPassword } from '../utils/switchPassword.js';

// The user's own account. An account without its email, or, with Projects on, without its
// Switch password, lands here first (router.js) and goes on to My lab once both are saved;
// anyone can come back to change them.
const router = useRouter();
const account = ref({});
const email = ref('');
const loading = ref(true);
const pending = ref(false);
// The onboarding step the account is at when the page opened: 'email', 'switch_password' or null
const step = ref(null);
const onboarding = ref(false);

const switchPassword = ref('');
const switchPasswordAgain = ref('');
const switchPending = ref(false);
const refused = ref([]);
const rules = computed(() => checkSwitchPassword(switchPassword.value, [account.value.username, account.value.switch_login]));
const same = computed(() => switchPassword.value === switchPasswordAgain.value);
const acceptable = computed(() => rules.value.every(rule => rule.passed) && same.value);

onMounted(async () => {
  const result = await getAccount();
  loading.value = false;
  if (!result.success) {
    toast.error(result.message, 'account');
    return;
  }
  account.value = result.data;
  email.value = result.data.email;
  step.value = onboardingStep(result.data);
  onboarding.value = step.value !== null;
});

// Once nothing is left to set, an account being onboarded goes on to My lab
const goOnIfDone = () => {
  step.value = onboardingStep(account.value);
  if (onboarding.value && step.value === null) router.push('/');
};

const save = async () => {
  pending.value = true;
  const result = await saveEmail(email.value);
  pending.value = false;
  if (!result.success) {
    toast.error(result.message, 'account');
    return;
  }
  account.value = result.data;
  email.value = result.data.email;
  toast.success('Email saved.');
  goOnIfDone();
};

const saveSwitch = async () => {
  if (!acceptable.value) return;
  switchPending.value = true;
  const result = await saveSwitchPassword(switchPassword.value);
  switchPending.value = false;
  if (!result.success) {
    refused.value = result.problems;
    if (!result.problems.length) toast.error(result.message, 'switch-password');
    return;
  }
  refused.value = [];
  switchPassword.value = '';
  switchPasswordAgain.value = '';
  account.value = { ...account.value, switch_password_set: true };
  if (result.failed.length) {
    const where = result.failed.map(a => a.mngt_IP).join(', ');
    toast.error(`Switch password saved. BLab couldn't set it yet on ${where}; it keeps trying.`, 'switch-password');
  } else {
    toast.success('Switch password saved. It is set on all your Switch accounts.');
  }
  goOnIfDone();
};
</script>
