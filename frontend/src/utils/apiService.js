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
  
  async reserve(switchId, endDate = null) {
    return baseApiCall(
      () => api.post(API_ENDPOINTS.RESERVE, { 
        switch: switchId, 
        end_date: endDate 
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

// Switch accounts: the caller's own SSH logins on the Switches they may work on
export const switchAccountService = {
  async getMine() {
    return baseApiCall(
      () => api.get(API_ENDPOINTS.SWITCH_ACCOUNTS),
      'fetch switch accounts'
    );
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
