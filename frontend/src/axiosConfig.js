import axios from 'axios';

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
  baseURL: 'https://10.69.144.180/api/', // Adjust the base URL as needed
  headers: {
    'X-CSRFToken': csrfToken, // Include CSRF token in every request
    'Authorization': `Token ${localStorage.getItem('token')}` // Include authentication token in every request
  },
  withCredentials: true // Ensure cookies are sent with requests
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

export default api;