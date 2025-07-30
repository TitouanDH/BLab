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
      status: error?.response?.status || 500
    };
  }
}

// Switch Management API calls
export const switchService = {
  async getAll() {
    return baseApiCall(
      () => api.get(API_ENDPOINTS.LIST_SWITCH),
      'fetch switches'
    );
  },
  
  async reserve(switchId, endDate = null) {
    return baseApiCall(
      () => api.post(API_ENDPOINTS.RESERVE, { 
        switch: switchId, 
        end_date: endDate 
      }),
      'reserve switch'
    );
  },
  
  async release(switchId, cleanup = false) {
    return baseApiCall(
      () => api.post(API_ENDPOINTS.RELEASE, { 
        switch: switchId, 
        cleanup: cleanup 
      }),
      'release switch'
    );
  }
};

// Reservation Management API calls
export const reservationService = {
  async getAll() {
    return baseApiCall(
      () => api.get(API_ENDPOINTS.LIST_RESERVATION),
      'fetch reservations'
    );
  }
};

// Port Management API calls
export const portService = {
  async getAll() {
    return baseApiCall(
      () => api.get(API_ENDPOINTS.LIST_PORT),
      'fetch ports'
    );
  },
  
  async getBySwitch(switchId) {
    return baseApiCall(
      () => api.get(`${API_ENDPOINTS.LIST_PORT}${switchId}/`),
      'fetch switch ports'
    );
  },
  
  async connect(portA, portB) {
    return baseApiCall(
      () => api.post(API_ENDPOINTS.CONNECT, { 
        portA: portA, 
        portB: portB 
      }),
      'connect ports'
    );
  },
  
  async disconnect(portA, portB) {
    return baseApiCall(
      () => api.post(API_ENDPOINTS.DISCONNECT, { 
        portA: portA, 
        portB: portB 
      }),
      'disconnect ports'
    );
  }
};

// User Management API calls
export const userService = {
  async getAll() {
    return baseApiCall(
      () => api.get(API_ENDPOINTS.LIST_USER),
      'fetch users'
    );
  },
  
  async getById(userId) {
    return baseApiCall(
      () => api.get(`${API_ENDPOINTS.LIST_USER}${userId}/`),
      'fetch user'
    );
  }
};

// Topology Sharing API calls
export const topologyService = {
  async share(targetUsername) {
    return baseApiCall(
      () => api.post(API_ENDPOINTS.SHARE_TOPOLOGY, { 
        target_username: targetUsername 
      }),
      'share topology'
    );
  },
  
  async getShared() {
    return baseApiCall(
      () => api.get(API_ENDPOINTS.LIST_SHARED_TOPOLOGIES),
      'fetch shared topologies'
    );
  },
  
  async unshare(shareId) {
    return baseApiCall(
      () => api.delete(`${API_ENDPOINTS.UNSHARE_TOPOLOGY}${shareId}/`),
      'unshare topology'
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
          message: handleApiError(result.reason, 'batch operation'),
          status: 500
        };
      }
    });
  } catch (error) {
    logError(error, 'batch api calls');
    throw error;
  }
}
