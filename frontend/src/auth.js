// Import the Axios instance from the Axios configuration file
import api from './axiosConfig.js';
import { handleApiError, logError } from './utils/errorHandler.js';
import { API_ENDPOINTS, STORAGE_KEYS } from './utils/constants.js';
import { reactive } from 'vue';

// Whether someone is logged in, for the parts of the page that outlive a navigation (the navbar)
export const session = reactive({ loggedIn: localStorage.getItem(STORAGE_KEYS.TOKEN) !== null });

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
    rememberEmail(response.data.user.email);
    session.loggedIn = true;
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
export async function signup(username, email, password) {
  try {
    const response = await api.post(API_ENDPOINTS.SIGNUP, {
      username: username,
      email: email,
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
      message: handleApiError(error, 'create the account')
    };
  }
}

export async function logout() {
  try {
      const response = await api.get(API_ENDPOINTS.LOGOUT);
      if (response.status === 200) {
          localStorage.clear();
          session.loggedIn = false;
          return { success: true };
      }
  } catch (error) {
      logError(error, 'logout');
  }
  
  // Always clear localStorage, even if the request fails
  localStorage.clear();
  session.loggedIn = false;
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

// Whether a user id from the API (a holder, say) is the current user
export function isMe(userId) {
  return userId !== null && userId !== undefined && String(userId) === String(getCurrentUserId());
}

// Every account needs an email, the user's Rainbow login: until it has one, BLab refuses
// everything but setting it (api/permissions.py). This browser remembers it once BLab said
// it is set, so pages don't have to ask each time.
export function rememberEmail(email) {
  if (email) localStorage.setItem(STORAGE_KEYS.EMAIL, email);
  else localStorage.removeItem(STORAGE_KEYS.EMAIL);
}

// The user's own account: { id, username, email }
export async function getAccount() {
  try {
    const response = await api.get(API_ENDPOINTS.ACCOUNT);
    rememberEmail(response.data.email);
    return { success: true, data: response.data };
  } catch (error) {
    logError(error, 'account');
    return { success: false, message: handleApiError(error, 'load your account'), status: error?.response?.status || 0 };
  }
}

export async function saveEmail(email) {
  try {
    const response = await api.post(API_ENDPOINTS.ACCOUNT, { email });
    rememberEmail(response.data.email);
    return { success: true, data: response.data };
  } catch (error) {
    logError(error, 'save email');
    return { success: false, message: handleApiError(error, 'save your email') };
  }
}

// Whether BLab says the account has no email yet, asking it when this browser doesn't know.
// If BLab can't say (unreachable, session ended), the page goes on and meets the error itself.
export async function emailMissing() {
  if (localStorage.getItem(STORAGE_KEYS.EMAIL)) return false;
  const result = await getAccount();
  return result.success && !result.data.email;
}
