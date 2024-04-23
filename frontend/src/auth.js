import axios from 'axios';

export function isAuthenticated() {
  const token = localStorage.getItem('token');
  return token !== null && token !== undefined;
}

export async function login(username, password) {
  try {
    const response = await axios.post('http://10.69.145.176:8000/api/login/', {
      username,
      password
    });
    const { token, user } = response.data;
    if (token) {
      localStorage.setItem('token', token);
      localStorage.setItem('user', user);
      return true;
    } else {
      console.error('Login failed:', response.data.detail);
      return false;
    }
  } catch (error) {
    console.error('Login error:', error);
    return false;
  }
}

// Function to sign up the user
export async function signup(username, password) {
  try {
    const response = await axios.post('http://10.69.145.176:8000/api/signup/', {
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

export function logout() {
  localStorage.removeItem('token');
  localStorage.removeItem('user');
}