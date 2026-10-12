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
    rememberAccount(response.data.user);
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

// Onboarding: every account needs an email, the user's Rainbow login, then, where BLab has
// Projects on, a Switch password. Until then BLab refuses everything but setting them
// (api/permissions.py). This browser remembers what BLab said is done, so pages don't have
// to ask each time.
export function rememberAccount(account) {
  if (account?.email) localStorage.setItem(STORAGE_KEYS.EMAIL, account.email);
  else localStorage.removeItem(STORAGE_KEYS.EMAIL);
  if (account && (!account.switch_password_enabled || account.switch_password_set)) {
    localStorage.setItem(STORAGE_KEYS.SWITCH_PASSWORD, 'done');
  } else {
    localStorage.removeItem(STORAGE_KEYS.SWITCH_PASSWORD);
  }
}

// What the account still needs: 'email', 'switch_password' or null
export function onboardingStep(account) {
  if (!account.email) return 'email';
  if (account.switch_password_enabled && !account.switch_password_set) return 'switch_password';
  return null;
}

// The user's own account: { id, username, email, switch_password_enabled, switch_password_set }
export async function getAccount() {
  try {
    const response = await api.get(API_ENDPOINTS.ACCOUNT);
    rememberAccount(response.data);
    return { success: true, data: response.data };
  } catch (error) {
    logError(error, 'account');
    return { success: false, message: handleApiError(error, 'load your account'), status: error?.response?.status || 0 };
  }
}

export async function saveEmail(email) {
  try {
    const response = await api.post(API_ENDPOINTS.ACCOUNT, { email });
    rememberAccount(response.data);
    return { success: true, data: response.data };
  } catch (error) {
    logError(error, 'save email');
    return { success: false, message: handleApiError(error, 'save your email') };
  }
}

// Chooses the Switch password: { success, failed } with the Switch accounts BLab couldn't reach
// yet (it keeps trying), or { success: false, problems } with each rule the password breaks
export async function saveSwitchPassword(password) {
  try {
    const response = await api.post(API_ENDPOINTS.SWITCH_PASSWORD, { password });
    localStorage.setItem(STORAGE_KEYS.SWITCH_PASSWORD, 'done');
    return { success: true, failed: response.data.failed || [] };
  } catch (error) {
    logError(error, 'save switch password');
    const problems = error?.response?.status === 400 ? error.response.data?.password : null;
    return { success: false, problems: problems || [], message: handleApiError(error, 'save your Switch password') };
  }
}

// Whether the account still has an onboarding step, asking BLab when this browser doesn't know.
// If BLab can't say (unreachable, session ended), the page goes on and meets the error itself.
export async function onboardingNeeded() {
  if (localStorage.getItem(STORAGE_KEYS.EMAIL) && localStorage.getItem(STORAGE_KEYS.SWITCH_PASSWORD)) return false;
  const result = await getAccount();
  return result.success && onboardingStep(result.data) !== null;
}
