import axios from 'axios';

// Obtain CSRF token from the CSRF cookie set by Django
const csrfToken = document.cookie.match(/csrftoken=([^;]+)/)[1];

// Set up Axios instance with default configuration
const api = axios.create({
  baseURL: 'https://127.0.0.1/api/', // Adjust the base URL as needed
  headers: {
    'X-CSRFToken': csrfToken, // Include CSRF token in every request
    'Authorization': `Token ${localStorage.getItem('token')}` // Include authentication token in every request
  }
});

// Add request interceptor
api.interceptors.request.use(
  config => {
    // Modify request config to include CSRF token and authentication token
    config.headers['X-CSRFToken'] = csrfToken;
    config.headers['Authorization'] = `Token ${localStorage.getItem('token')}`;
    return config;
  },
  error => {
    // Handle request errors
    return Promise.reject(error);
  }
);

export default api;
