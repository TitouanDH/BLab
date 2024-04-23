import { createRouter, createWebHistory } from 'vue-router';
import Home from './pages/Home.vue';
import Reservation from './pages/Reservation.vue';
import Login from './pages/Login.vue';
import Signup from './pages/Signup.vue';
import Topology from './pages/Topology.vue';
import { isAuthenticated } from './auth'; // Import isAuthenticated function from auth.js

const routes = [
  {
    path: '/',
    component: Home,
  },
  {
    path: '/login',
    component: Login,
  },
  {
    path: '/signup',
    component: Signup,
  },
  {
    path: '/reservation',
    component: Reservation,
    meta: { requiresAuth: true }, // This route requires authentication
  },
  {
    path: '/topology',
    component: Topology,
    meta: { requiresAuth: true }, // This route requires authentication
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

// Navigation guard to check if route requires authentication
router.beforeEach((to, from, next) => {
  if (to.meta.requiresAuth && !isAuthenticated()) { // Use isAuthenticated function to check authentication
    // Redirect to login page if not authenticated
    next('/login');
  } else {
    next(); // Continue to the requested route
  }
});

export default router;