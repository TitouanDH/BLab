// Background refresh of a page. `load` returns an apiService result ({ success, status })
// or an array of them. A poll that cannot reach BLab (no answer, or a 5xx from the proxy)
// shows the discreet "Cannot reach BLab" toast instead of only logging; the next poll that
// works removes it. Other failures are the page's to handle.
//
//   usePoll(fetchSwitches, 2000);              // first load on mount, then every 2 s
//   usePoll(load, 15000, { immediate: false }); // the page loads itself first
//
// A poll and a reload after an action can be in flight together, and answered in either
// order: a load numbers its calls with latestCall() and drops an answer a newer call has
// overtaken, else an older answer arriving last brings the old state back until the next poll.
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

// Numbers the calls of a load: `const isLatest = latest();` at its start, then after each
// answer `if (!isLatest()) return [];` (a newer call has started: its answer is the one to use).
//
//   const latest = latestCall();
//   const load = async () => {
//     const isLatest = latest();
//     const result = await service.get();
//     if (!isLatest()) return [];
//     ...
export function latestCall() {
  let last = 0;
  return () => {
    const mine = ++last;
    return () => mine === last;
  };
}

// `refresh` always loads, even while a poll is in flight (the state changed since it left), so
// a load called through it must drop overtaken answers (latestCall). The poll itself skips a
// tick while a load runs: a slow answer doesn't pile requests up.
export function usePoll(load, intervalMs, { immediate = true } = {}) {
  let timer = null;
  let running = 0;
  const refresh = async () => {
    running += 1;
    try {
      reportPoll(await load());
    } finally {
      running -= 1;
    }
  };
  const tick = () => (running ? undefined : refresh());
  onMounted(() => {
    if (immediate) tick();
    timer = setInterval(tick, intervalMs);
  });
  onBeforeUnmount(() => clearInterval(timer));
  return { refresh };
}
