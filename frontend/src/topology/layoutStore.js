// Where the Topology canvas keeps the Switch positions a user arranged: per Topology owner,
// one position per Switch (its ports follow it, see model.js portOffsets). The canvas saves
// them when a drag ends and clears them on Re-arrange; with none saved it lays the Switches
// out tidily.
//
// For now they live in this browser's localStorage. Saving them on the server, so a shared
// Topology looks the same to everyone, only has to change this module (#29).

// v2: positions per Switch. The earlier per-port positions (topologyLayout_<owner>) are
// left alone and no longer read: they mostly held the old tall columns.
const key = (ownerId) => `topologyLayout.v2_${ownerId}`;

export const layoutStore = {
  /** @returns {Promise<{ [switchId]: {x: number, y: number} }>} */
  async load(ownerId) {
    try {
      return JSON.parse(localStorage.getItem(key(ownerId))) || {};
    } catch {
      return {};
    }
  },

  /** @param {{ [switchId]: {x: number, y: number} }} positions - every Switch drawn */
  async save(ownerId, positions) {
    localStorage.setItem(key(ownerId), JSON.stringify(positions));
  },

  async clear(ownerId) {
    localStorage.removeItem(key(ownerId));
  },
};
