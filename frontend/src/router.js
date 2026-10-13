// router.js

import { createRouter, createWebHistory } from 'vue-router';
import MyLab from './pages/MyLab.vue';
import Reservation from './pages/Reservation.vue';
import Login from './pages/Login.vue';
import Signup from './pages/Signup.vue';
import Topology from './pages/Topology.vue';
import LabStatus from './pages/LabStatus.vue';
import Account from './pages/Account.vue';
import Projects from './pages/Projects.vue';
import Join from './pages/Join.vue';
import { isAuthenticated, onboardingNeeded } from './auth';

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
    path: '/projects',
    component: Projects,
    meta: { requiresAuth: true },
  },
  {
    // An Invite link: a confirm page, then Join. A logged-out visitor logs in first and comes back.
    path: '/join/:token',
    component: Join,
    meta: { requiresAuth: true },
  },
  {
    path: '/account',
    component: Account,
    meta: { requiresAuth: true }, // onboarding: the one page open to an account without its email or Switch password
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

// Pages that need a session send logged-out visitors to the log in page, and an account
// without its email or, with Projects on, its Switch password to the account page until set.
// Both come back to the page asked for (?redirect), such as an Invite link.
const back = (to) => (to.fullPath === '/' ? {} : { redirect: to.fullPath });
router.beforeEach(async (to) => {
  if (!to.meta.requiresAuth) return true;
  if (!isAuthenticated()) return { path: '/login', query: back(to) };
  if (to.path !== '/account' && (await onboardingNeeded())) return { path: '/account', query: back(to) };
  return true;
});

export default router;
