// Background refresh of a page. `load` returns an apiService result ({ success, status })
// or an array of them. A poll that cannot reach BLab (no answer, or a 5xx from the proxy)
// shows the discreet "Cannot reach BLab" toast instead of only logging; the next poll that
// works removes it. Other failures are the page's to handle.
//
//   usePoll(fetchSwitches, 2000);              // first load on mount, then every 2 s
//   usePoll(load, 15000, { immediate: false }); // the page loads itself first
import { onBeforeUnmount, onMounted } from 'vue';
import { toast } from './toast.js';

export function isUnreachable(result) {
  return !!result && result.success === false && (!result.status || result.status >= 500);
}

export function reportPoll(results) {
  const list = (Array.isArray(results) ? results : [results]).filter(Boolean);
  if (list.some(isUnreachable)) toast.unreachable();
  else if (list.length && list.every(r => r.success)) toast.reachable();
}

export function usePoll(load, intervalMs, { immediate = true } = {}) {
  let timer = null;
  let running = false;
  const tick = async () => {
    if (running) return;  // a slow answer: don't pile requests up
    running = true;
    try {
      reportPoll(await load());
    } finally {
      running = false;
    }
  };
  onMounted(() => {
    if (immediate) tick();
    timer = setInterval(tick, intervalMs);
  });
  onBeforeUnmount(() => clearInterval(timer));
  return { refresh: tick };
}
