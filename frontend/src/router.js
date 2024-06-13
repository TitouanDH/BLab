// router.js

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
  console.log('Navigation guard: ', to.path);
  if (to.meta.requiresAuth) {
    console.log('Route requires auth');
    if (!isAuthenticated()) {
      console.log('Not authenticated, redirecting to login');
      // Redirect to login page if not authenticated
      next('/login');
    } else {
      console.log('Authenticated, proceeding to route');
      next(); // Continue to the requested route
    }
  } else {
    next(); // Continue to the requested route
  }
});

export default router;
