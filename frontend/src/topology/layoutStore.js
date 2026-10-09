// Where the Topology canvas keeps the Switch positions arranged on a Topology: per Topology
// owner, one position per Switch (its ports follow it, see model.js portOffsets). The canvas
// saves them when a drag ends and forgets them on Re-arrange; with none saved it lays the
// Switches out tidily.
//
// They live on the server, so a shared Topology looks the same to everyone viewing it: read
// with the Topology ("layout" in topology/<owner>/), saved and forgotten through
// topology/<owner>/layout/ by whoever may work on it. Requests for one Topology go one after
// the other, so a Re-arrange is never undone by a save still on its way.
import { topologyService } from '../utils/apiService.js';
import { toast } from '../composables/toast.js';

const SAVE_DELAY = 400;  // ms after a drag ends: drags in a row make one save

// Before #29 each browser kept its own layout here. Uploaded once if the server has none,
// then dropped.
const browserKey = (owner) => `topologyLayout.v2_${owner}`;

// By owner id, as a string
const pending = new Map();   // { timer, positions }: a save not sent yet
const queues = new Map();    // the last request sent, for the next to wait on
const uploads = new Map();   // the upload of the browser's layout, while it runs
const uploaded = new Set();  // owners whose browser layout this page has handled

// Sends requests for one Topology in order
function enqueue(owner, request) {
  const next = (queues.get(owner) || Promise.resolve()).then(request);
  queues.set(owner, next);
  return next;
}

function browserLayout(owner) {
  try {
    const positions = JSON.parse(localStorage.getItem(browserKey(owner)));
    return positions && typeof positions === 'object' && Object.keys(positions).length ? positions : null;
  } catch {
    return null;
  }
}

// The browser's own layout, sent once if the server has none and then dropped
function uploadBrowserLayout(owner, mayWork) {
  if (uploaded.has(owner)) return null;
  const positions = browserLayout(owner);
  if (!positions) {
    localStorage.removeItem(browserKey(owner));
    uploaded.add(owner);
    return null;
  }
  if (!mayWork) return positions;  // drawn, and kept until someone who may work on it opens it
  if (!uploads.has(owner)) {
    uploads.set(owner, enqueue(owner, () => topologyService.saveLayout(owner, positions)).then((result) => {
      if (result.success) localStorage.removeItem(browserKey(owner));
      uploaded.add(owner);  // tried once per visit: a failure leaves the key for the next one
      uploads.delete(owner);
      return positions;
    }));
  }
  return uploads.get(owner);
}

function sendSave(owner) {
  const save = pending.get(owner);
  if (!save) return;
  clearTimeout(save.timer);
  pending.delete(owner);
  enqueue(owner, () => topologyService.saveLayout(owner, save.positions)).then((result) => {
    if (!result.success) toast.error(`Couldn't save where the Switches are. ${result.message}`, 'topology-layout');
  });
}

// A drag just before leaving the page is saved all the same
if (typeof window !== 'undefined') window.addEventListener('pagehide', () => layoutStore.flush());

export const layoutStore = {
  /**
   * The positions to draw a Topology with, from its API answer, including a save not sent yet
   * @param {{ layout, may_work }} topology - the answer of topology/<owner>/
   * @returns {Promise<{ [switchId]: {x: number, y: number} }>}
   */
  async load(ownerId, topology) {
    const owner = String(ownerId);
    let saved = topology.layout || {};
    if (!Object.keys(saved).length) {
      saved = (await uploadBrowserLayout(owner, topology.may_work)) || {};
    } else if (!uploaded.has(owner)) {
      // The server's layout wins: this browser's is no longer needed
      localStorage.removeItem(browserKey(owner));
      uploaded.add(owner);
    }
    return { ...saved, ...pending.get(owner)?.positions };
  },

  /** Saves shortly after, so a few drags in a row make one request
   * @param {{ [switchId]: {x: number, y: number} }} positions - every Switch drawn */
  save(ownerId, positions) {
    const owner = String(ownerId);
    clearTimeout(pending.get(owner)?.timer);
    pending.set(owner, { timer: setTimeout(() => sendSave(owner), SAVE_DELAY), positions });
  },

  /** Sends every save still waiting, e.g. when leaving the page */
  flush() {
    for (const owner of [...pending.keys()]) sendSave(owner);
  },

  /** Forgets the saved positions (Re-arrange), for everyone viewing the Topology */
  async forget(ownerId) {
    const owner = String(ownerId);
    clearTimeout(pending.get(owner)?.timer);
    pending.delete(owner);
    const result = await enqueue(owner, () => topologyService.forgetLayout(owner));
    if (!result.success) toast.error(`Couldn't Re-arrange for everyone. ${result.message}`, 'topology-layout');
  },
};
