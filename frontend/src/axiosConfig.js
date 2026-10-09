import axios from 'axios';
import { STORAGE_KEYS } from './utils/constants.js';

// Function to get the CSRF token from cookies
const getCsrfToken = () => {
  const match = document.cookie.match(/csrftoken=([^;]+)/);
  return match ? match[1] : null;
};

// Check if CSRF token is available
const csrfToken = getCsrfToken();
if (!csrfToken) {
  console.error('CSRF token not found. Please ensure you are logged in.');
}

// Set up Axios instance with default configuration
const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api/',
  headers: {
    'X-CSRFToken': csrfToken,
    'Authorization': `Token ${localStorage.getItem('token')}`
  },
  withCredentials: true
});

// Add request interceptor
api.interceptors.request.use(
  config => {
    // Modify request config to include CSRF token and authentication token
    const token = localStorage.getItem('token');
    const csrfToken = getCsrfToken();
    if (csrfToken) {
      config.headers['X-CSRFToken'] = csrfToken;
    } else {
      console.error('CSRF token not found. Requests may fail.');
    }
    if (token) {
      config.headers['Authorization'] = `Token ${token}`;
    } else {
      console.error('Authentication token not found. Requests may fail.');
    }
    return config;
  },
  error => {
    // Handle request errors
    return Promise.reject(error);
  }
);

// BLab refuses an account without its email (api/permissions.py), say because the email was
// removed after this browser remembered it: forget it, and go set it
api.interceptors.response.use(
  response => response,
  error => {
    if (error?.response?.status === 403 && error.response.data?.code === 'email_required') {
      localStorage.removeItem(STORAGE_KEYS.EMAIL);
      import('./router.js').then(({ default: router }) => router.push('/account'));
    }
    return Promise.reject(error);
  }
);

export default api;