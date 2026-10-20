// router.js

import { createRouter, createWebHistory } from 'vue-router';
import MyLab from './pages/MyLab.vue';
import Reservation from './pages/Reservation.vue';
import Login from './pages/Login.vue';
import Signup from './pages/Signup.vue';
import Topology from './pages/Topology.vue';
import TopologyPicker from './pages/TopologyPicker.vue';
import LabStatus from './pages/LabStatus.vue';
import Account from './pages/Account.vue';
import Projects from './pages/Projects.vue';
import Project from './pages/Project.vue';
import ProjectSwitches from './pages/project/ProjectSwitches.vue';
import ProjectSnapshots from './pages/project/ProjectSnapshots.vue';
import ProjectMembers from './pages/project/ProjectMembers.vue';
import Join from './pages/Join.vue';
import Contribute from './pages/Contribute.vue';
import { isAuthenticated, onboardingNeeded, session } from './auth';

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
    // The Topology picker, with Projects off. With them on, a Topology is a Project's picture: the
    // Projects list. Older links (?project=<id>, ?owner=<id>) go to the page they meant; while
    // this browser doesn't know Projects are on, the picker asks BLab and takes ?project along.
    path: '/topology',
    component: TopologyPicker,
    meta: { requiresAuth: true },
    beforeEnter: (to) => {
      if (session.projects && /^\d+$/.test(to.query.project || '')) return { path: `/projects/${to.query.project}`, replace: true };
      if (/^\d+$/.test(to.query.owner || '')) return { path: `/topology/user/${to.query.owner}`, replace: true };
      if (session.projects) return { path: '/projects', replace: true };
      return true;
    },
  },
  {
    // A Project's Topology, bookmarked before it moved onto the Project page: through the
    // picker's address, which knows whether BLab has Projects
    path: '/topology/:project(\\d+)',
    redirect: (to) => ({ path: '/topology', query: { project: to.params.project } }),
  },
  {
    // A user's Topology, with Projects off: my own or one shared with me; the canvas fills the
    // space under the navbar
    path: '/topology/user/:owner(\\d+)',
    component: Topology,
    meta: { requiresAuth: true, fill: true },
  },
  {
    path: '/status',
    component: LabStatus,
    meta: { requiresAuth: true },
  },
  {
    // Contribute a cable (#58): ?switch=<id> picks the Switch
    path: '/contribute',
    component: Contribute,
    meta: { requiresAuth: true },
  },
  {
    path: '/projects',
    component: Projects,
    meta: { requiresAuth: true },
  },
  {
    // A Project's page (with Projects on): its Topology, Switches, Snapshots and Members. The
    // Topology canvas fills the space under the navbar; the other sections scroll.
    path: '/projects/:id(\\d+)',
    component: Project,
    meta: { requiresAuth: true },
    children: [
      { path: '', component: Topology, meta: { fill: true } },
      { path: 'switches', component: ProjectSwitches },
      { path: 'snapshots', component: ProjectSnapshots },
      { path: 'members', component: ProjectMembers },
    ],
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
