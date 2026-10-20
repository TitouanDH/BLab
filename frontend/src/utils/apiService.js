/**
 * API Service Layer
 * Centralized API calls with consistent error handling and loading states
 */

import api from '../axiosConfig.js';
import { handleApiError, logError } from './errorHandler.js';
import { API_ENDPOINTS } from './constants.js';

/**
 * Base API call wrapper with error handling
 * @param {Function} apiCall - The API call function
 * @param {string} context - Context for error logging
 * @returns {Promise<Object>} - Standardized response object
 */
async function baseApiCall(apiCall, context = 'api') {
  try {
    const response = await apiCall();
    return {
      success: true,
      data: response.data,
      status: response.status
    };
  } catch (error) {
    logError(error, context);
    return {
      success: false,
      message: handleApiError(error, context),
      status: error?.response?.status || 0  // 0: no answer, BLab unreachable
    };
  }
}

// Switch Management API calls
export const switchService = {
  async getAll() {
    return baseApiCall(
      () => api.get(API_ENDPOINTS.LIST_SWITCH),
      'load the Switches'
    );
  },
  
  // With Projects on, `project` says which one: { project: id }, { new_project: { name, reference } },
  // or nothing for the last used one (a first Reserve makes one)
  async reserve(switchId, endDate = null, project = {}) {
    return baseApiCall(
      () => api.post(API_ENDPOINTS.RESERVE, {
        switch: switchId,
        end_date: endDate,
        ...project
      }),
      'reserve this Switch'
    );
  },
  
  // Every Release Cleans up (see CONTEXT.md)
  async release(switchId) {
    return baseApiCall(
      () => api.post(API_ENDPOINTS.RELEASE, { switch: switchId }),
      'Release this Switch'
    );
  },

  // Renewal: pushes the Reservation's end date back by a week, at most twice
  async renew(switchId) {
    return baseApiCall(
      () => api.post(API_ENDPOINTS.RENEW, { switch: switchId }),
      'Renew this Reservation'
    );
  },

  // The ports that would count as Unwanted cables once the Switch is released
  async releaseCheck(switchId) {
    return baseApiCall(
      () => api.get(`${API_ENDPOINTS.RELEASE_CHECK}${switchId}/`),
      'check the Switch before its Release'
    );
  }
};

// Lab status: every Switch, its holder and its last Inspection
export const labStatusService = {
  async get() {
    return baseApiCall(
      () => api.get(API_ENDPOINTS.LAB_STATUS),
      'load the Lab status'
    );
  },

  // Re-check: an Inspection that lifts the Quarantine if the Switch is clean
  async recheck(switchId) {
    return baseApiCall(
      () => api.post(API_ENDPOINTS.RECHECK, { switch: switchId }),
      'Re-check this Switch'
    );
  }
};

// Contribute a cable (#58): the Switches I may add a cable to, the cable to plug, and BLab's check
export const contributeService = {
  async switches() {
    return baseApiCall(() => api.get(API_ENDPOINTS.CONTRIBUTE_CABLE), 'load the Switches');
  },

  async propose(switchId, backbone = null) {
    const params = { switch: switchId, ...(backbone ? { backbone } : {}) };
    return baseApiCall(() => api.get(API_ENDPOINTS.CONTRIBUTE_CABLE, { params }), 'propose a cable');
  },

  // Once plugged: BLab checks the cable joins those two ports, then records the Port
  async plugged(cable) {
    return baseApiCall(() => api.post(API_ENDPOINTS.CONTRIBUTE_CABLE, cable), 'check the cable');
  }
};

// My lab: every Switch the caller may work on, grouped by Project (or, with Projects off, by
// Topology), with their Switch accounts, and the Quarantines naming them (api/api/my_lab.py)
export const myLabService = {
  async get() {
    return baseApiCall(() => api.get(API_ENDPOINTS.MY_LAB), 'load My lab');
  }
};

// Reservation Management API calls
export const reservationService = {
  async getAll() {
    return baseApiCall(
      () => api.get(API_ENDPOINTS.LIST_RESERVATION),
      'load the Reservations'
    );
  }
};

// Port Management API calls
export const portService = {
  async getAll() {
    return baseApiCall(
      () => api.get(API_ENDPOINTS.LIST_PORT),
      'load the ports'
    );
  },
  
  async getBySwitch(switchId) {
    return baseApiCall(
      () => api.get(`${API_ENDPOINTS.LIST_PORT}${switchId}/`),
      'load the Switch ports'
    );
  },
  
  async connect(portA, portB) {
    return baseApiCall(
      () => api.post(API_ENDPOINTS.CONNECT, { 
        portA: portA, 
        portB: portB 
      }),
      'connect the ports'
    );
  },
  
  async disconnect(portA, portB) {
    return baseApiCall(
      () => api.post(API_ENDPOINTS.DISCONNECT, { 
        portA: portA, 
        portB: portB 
      }),
      'disconnect the Link'
    );
  }
};

// User Management API calls
export const userService = {
  async getAll() {
    return baseApiCall(
      () => api.get(API_ENDPOINTS.LIST_USER),
      'load the users'
    );
  },
  
  async getById(userId) {
    return baseApiCall(
      () => api.get(`${API_ENDPOINTS.LIST_USER}${userId}/`),
      'load the user'
    );
  }
};

// Topology and sharing API calls
export const topologyService = {
  // A user's Switches, their Ports and every Link with an end on them
  async get(ownerId) {
    return baseApiCall(
      () => api.get(`${API_ENDPOINTS.TOPOLOGY}${ownerId}/`),
      'load the Topology'
    );
  },

  async share(targetUsername) {
    return baseApiCall(
      () => api.post(API_ENDPOINTS.SHARE_TOPOLOGY, { 
        target_username: targetUsername 
      }),
      'share your Topology'
    );
  },
  
  async getShared() {
    return baseApiCall(
      () => api.get(API_ENDPOINTS.LIST_SHARED_TOPOLOGIES),
      'load the shared Topologies'
    );
  },
  
  // The Topology layout (read with the Topology, as "layout"): positions { [switchId]: {x, y} }
  async saveLayout(ownerId, positions) {
    return baseApiCall(
      () => api.put(`${API_ENDPOINTS.TOPOLOGY}${ownerId}/layout/`, { positions }),
      'save the layout'
    );
  },

  async forgetLayout(ownerId) {
    return baseApiCall(
      () => api.delete(`${API_ENDPOINTS.TOPOLOGY}${ownerId}/layout/`),
      'Re-arrange the Topology'
    );
  },

  async unshare(shareId) {
    return baseApiCall(
      () => api.delete(`${API_ENDPOINTS.UNSHARE_TOPOLOGY}${shareId}/`),
      'stop sharing the Topology'
    );
  }
};

// Projects (CONTEXT.md), where BLab has them on (BLAB_PROJECTS): every endpoint answers 404 when off
export const projectService = {
  // { projects: [{ id, name, reference, owner, is_owner, archived_at, switch_count, members, invite_token }], last_used }
  async list() {
    return baseApiCall(() => api.get(API_ENDPOINTS.PROJECTS), 'load your Projects');
  },

  async create(name, reference = '') {
    return baseApiCall(() => api.post(API_ENDPOINTS.PROJECTS, { name, reference }), 'create the Project');
  },

  // A Project's Topology: its Switches, their Ports, every Link with an end on them, and its layout
  async topology(projectId) {
    return baseApiCall(() => api.get(`${API_ENDPOINTS.PROJECTS}${projectId}/topology/`), 'load the Topology');
  },

  async saveLayout(projectId, positions) {
    return baseApiCall(() => api.put(`${API_ENDPOINTS.PROJECTS}${projectId}/layout/`, { positions }), 'save the layout');
  },

  async forgetLayout(projectId) {
    return baseApiCall(() => api.delete(`${API_ENDPOINTS.PROJECTS}${projectId}/layout/`), 'Re-arrange the Topology');
  },

  // The owner gives the Project a new Invite link: the old one stops working
  async renewInvite(projectId) {
    return baseApiCall(() => api.post(`${API_ENDPOINTS.PROJECTS}${projectId}/invite/`), 'renew the Invite link');
  },

  async leave(projectId) {
    return baseApiCall(() => api.post(`${API_ENDPOINTS.PROJECTS}${projectId}/leave/`), 'leave the Project');
  },

  async removeMember(projectId, userId) {
    return baseApiCall(() => api.delete(`${API_ENDPOINTS.PROJECTS}${projectId}/members/${userId}/`), 'remove the member');
  },

  async archive(projectId) {
    return baseApiCall(() => api.post(`${API_ENDPOINTS.PROJECTS}${projectId}/archive/`), 'archive the Project');
  },

  // Moves a reserved Switch into this Project, without a Release: { detail, project }
  async moveSwitch(projectId, switchId) {
    return baseApiCall(() => api.post(`${API_ENDPOINTS.PROJECTS}${projectId}/switches/`, { switch: switchId }), 'move the Switch');
  },

  async get(projectId) {
    return baseApiCall(() => api.get(`${API_ENDPOINTS.PROJECTS}${projectId}/`), 'load the Project');
  },

  // { snapshots: [{ id, taken_at, taken_by, reason, note, switches, links }], kept, restore }
  async snapshots(projectId) {
    return baseApiCall(() => api.get(`${API_ENDPOINTS.PROJECTS}${projectId}/snapshots/`), 'load the Snapshots');
  },

  // Save: a Snapshot of all the Project's Switches now
  async saveSnapshot(projectId, note = '') {
    return baseApiCall(() => api.post(`${API_ENDPOINTS.PROJECTS}${projectId}/snapshots/`, { note }), 'save a Snapshot');
  },

  // What a Restore would do: { switches: [{ id, model, state, detail, free_on, release_differs, ... }], links }
  async restorePlan(projectId, snapshotId) {
    return baseApiCall(() => api.get(`${API_ENDPOINTS.PROJECTS}${projectId}/snapshots/${snapshotId}/restore/`),
      'read what a Restore would do');
  },

  async restore(projectId, snapshotId, switches) {
    return baseApiCall(() => api.post(`${API_ENDPOINTS.PROJECTS}${projectId}/snapshots/${snapshotId}/restore/`, { switches }),
      'Restore the Snapshot');
  },

  // What an Invite link opens: { project: { name, reference, owner, members, switches }, member }
  async preview(token) {
    return baseApiCall(() => api.get(`${API_ENDPOINTS.JOIN}${encodeURIComponent(token)}/`), 'open the Invite link');
  },

  async join(token) {
    return baseApiCall(() => api.post(`${API_ENDPOINTS.JOIN}${encodeURIComponent(token)}/`), 'join the Project');
  }
};

/**
 * Batch API calls with error handling
 * @param {Array} apiCalls - Array of API call promises
 * @returns {Promise<Array>} - Array of results
 */
export async function batchApiCalls(apiCalls) {
  try {
    const results = await Promise.allSettled(apiCalls);
    return results.map(result => {
      if (result.status === 'fulfilled') {
        return result.value;
      } else {
        logError(result.reason, 'batch api call');
        return {
          success: false,
          message: handleApiError(result.reason, 'do this'),
          status: result.reason?.response?.status || 0
        };
      }
    });
  } catch (error) {
    logError(error, 'batch api calls');
    throw error;
  }
}
