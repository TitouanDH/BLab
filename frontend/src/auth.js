// Import the Axios instance from the Axios configuration file
import api from './axiosConfig.js';
import { handleApiError, logError } from './utils/errorHandler.js';
import { API_ENDPOINTS, STORAGE_KEYS } from './utils/constants.js';

export function isAuthenticated() {
  const token = localStorage.getItem(STORAGE_KEYS.TOKEN);
  return token !== null && token !== undefined;
}

export async function login(username, password) {
  try {
    const response = await api.post(API_ENDPOINTS.LOGIN, {
      username,
      password
    });
    localStorage.setItem(STORAGE_KEYS.TOKEN, response.data.token);
    localStorage.setItem(STORAGE_KEYS.USER, response.data.user.id);
    localStorage.setItem(STORAGE_KEYS.IS_STAFF, response.data.is_staff);
    return { success: true };
  } catch (error) {
    logError(error, 'login');
    return { 
      success: false, 
      message: handleApiError(error, 'log in')
    };
  }
}

// Function to sign up the user
export async function signup(username, password) {
  try {
    const response = await api.post(API_ENDPOINTS.SIGNUP, {
      username: username,
      password: password
    });
    const data = response.data;
    if (response.status === 201) {
      localStorage.setItem(STORAGE_KEYS.TOKEN, data.token);
      localStorage.setItem(STORAGE_KEYS.USER, data.user.id);
      return { success: true };
    } else {
      return { 
        success: false, 
        message: data.detail || 'Signup failed'
      };
    }
  } catch (error) {
    logError(error, 'signup');
    return { 
      success: false, 
      message: handleApiError(error, 'create account')
    };
  }
}

export async function logout() {
  try {
      const response = await api.get(API_ENDPOINTS.LOGOUT);
      if (response.status === 200) {
          localStorage.clear();
          console.log(response.data.detail);
          return { success: true };
      }
  } catch (error) {
      logError(error, 'logout');
  }
  
  // Always clear localStorage, even if the request fails
  localStorage.clear();
  return { success: false };
}

// Add a helper function to check admin status
export function isAdmin() {
  return localStorage.getItem(STORAGE_KEYS.IS_STAFF) === 'true';
}

// Helper function to get current user ID
export function getCurrentUserId() {
  return localStorage.getItem(STORAGE_KEYS.USER);
}