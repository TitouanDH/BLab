// router.js

import { createRouter, createWebHistory } from 'vue-router';
import MyLab from './pages/MyLab.vue';
import Reservation from './pages/Reservation.vue';
import Login from './pages/Login.vue';
import Signup from './pages/Signup.vue';
import Topology from './pages/Topology.vue';
import LabStatus from './pages/LabStatus.vue';
import Account from './pages/Account.vue';
import { emailMissing, isAuthenticated } from './auth';

const routes = [
  {
    path: '/',
    component: MyLab,
    meta: { requiresAuth: true }, // logged-out visitors go to the login page
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
    meta: { requiresAuth: true, fill: true }, // the canvas fills the space under the navbar
  },
  {
    path: '/status',
    component: LabStatus,
    meta: { requiresAuth: true },
  },
  {
    path: '/account',
    component: Account,
    meta: { requiresAuth: true }, // where the email is set: the one page open to an account without it
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

// Pages that need a session send logged-out visitors to the log in page, and an account
// without its email to the account page until it is set
router.beforeEach(async (to) => {
  if (!to.meta.requiresAuth) return true;
  if (!isAuthenticated()) return '/login';
  if (to.path !== '/account' && (await emailMissing())) return '/account';
  return true;
});

export default router;
