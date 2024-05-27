import axios from 'axios';


// Set up Axios instance with default configuration
const api = axios.create({
  baseURL: 'https://10.69.145.176/api/', // Adjust the base URL as needed
  headers: {
    'Authorization': `Token ${localStorage.getItem('token')}` // Include authentication token in every request
  },
  withCredentials: true // Ensure cookies are sent with requests
});

// Add request interceptor
api.interceptors.request.use(
  config => {
    // Modify request config to include CSRF token and authentication token
    const token = localStorage.getItem('token');
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
