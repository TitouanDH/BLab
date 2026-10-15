// Feedback rule (see #27): the result of an action is a toast. Success toasts go away by
// themselves; errors stay until closed. A failing background poll shows one discreet
// "Cannot reach BLab" toast, gone again at the next poll that works.
//
//   import { toast } from '../composables/toast.js';
//   toast.success('Reservation successful.');
//   toast.error(`Couldn't reserve this Switch. ${result.message}`);
//
// <UiToasts/> in App.vue shows them.
import { reactive } from 'vue';

const SUCCESS_MS = 5000;
const UNREACHABLE_KEY = 'unreachable';

export const toasts = reactive([]);
let nextId = 1;

function push(kind, message, key = null) {
  if (key) {
    const existing = toasts.find(t => t.key === key);
    if (existing) return existing.id;
  }
  const id = nextId++;
  toasts.push({ id, kind, message, key });
  if (kind === 'success') setTimeout(() => dismiss(id), SUCCESS_MS);
  return id;
}

export function dismiss(id) {
  const index = toasts.findIndex(t => t.id === id);
  if (index !== -1) toasts.splice(index, 1);
}

function dismissKey(key) {
  const t = toasts.find(t => t.key === key);
  if (t) dismiss(t.id);
}

export const toast = {
  success: (message) => push('success', message),
  // key: at most one such error on screen at a time
  error: (message, key = null) => push('error', message, key),
  // Called by background polls (see poll.js)
  unreachable: () => push('quiet', 'Cannot reach BLab. Trying again...', UNREACHABLE_KEY),
  reachable: () => dismissKey(UNREACHABLE_KEY),
};
