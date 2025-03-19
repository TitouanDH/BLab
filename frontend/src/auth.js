// Import the Axios instance from the Axios configuration file
import api from './axiosConfig.js';

export function isAuthenticated() {
  const token = localStorage.getItem('token');
  return token !== null && token !== undefined;
}

export async function login(username, password) {
  try {
    const response = await api.post('login/', {
      username,
      password
    });
    localStorage.setItem('token', response.data.token);
    localStorage.setItem('user', response.data.user);
    localStorage.setItem('is_staff', response.data.is_staff);
    return true;
  } catch (error) {
    console.error('Login failed:', error);
    return false;
  }
}

// Function to sign up the user
export async function signup(username, password) {
  try {
    const response = await api.post('signup/', {
      username: username,
      password: password
    });
    const data = response.data;
    if (response.status === 201) {
      localStorage.setItem('token', data.token);
      localStorage.setItem('user', data.user);
      return true;
    } else {
      console.error(data.detail);
      return false;
    }
  } catch (error) {
    console.error(error);
    return false;
  }
}

export async function logout() {
  try {
      const response = await api.get('logout/');
      if (response.status === 200) {
          localStorage.clear();
          console.log(response.data.detail);
          return true;
      }
  } catch (error) {
      console.error("Logout request failed:", error);  // More specific error logging
  }
  
  // Always clear localStorage, even if the request fails
  localStorage.clear();
  return false;
}

// Add a helper function to check admin status
export function isAdmin() {
  return localStorage.getItem('is_staff') === 'true';
}