// Projects (CONTEXT.md), where BLab has them on (BLAB_PROJECTS). BLab says so itself: projects/
// answers 404 while they are off. What it last said is kept in session.projects, for the navbar.
import { session } from '../auth.js';
import { projectService } from './apiService.js';
import { STORAGE_KEYS } from './constants.js';

export function rememberProjects(enabled) {
  session.projects = enabled;
  localStorage.setItem(STORAGE_KEYS.PROJECTS, enabled ? 'on' : 'off');
}

/**
 * The user's Projects, and whether this stage has them at all.
 * @returns {Promise<{ enabled: boolean|null, projects: Array, active: Array, lastUsed: number|null, result: Object }>}
 *   enabled is null when BLab couldn't say (unreachable, refused); result is the API result
 */
export async function loadProjects() {
  const result = await projectService.list();
  if (result.status === 404) {
    rememberProjects(false);
    return { enabled: false, projects: [], active: [], lastUsed: null, result: { ...result, success: true } };
  }
  if (!result.success) return { enabled: null, projects: [], active: [], lastUsed: null, result };
  rememberProjects(true);
  const projects = result.data.projects || [];
  return { enabled: true, projects, active: projects.filter(p => !p.archived_at), lastUsed: result.data.last_used, result };
}

// A Switch belongs to a Project only through a Reservation: the Reservation page, this Project chosen
export const reserveIntoPath = (projectId) => ({ path: '/reservation', query: { project: String(projectId) } });
export const RESERVE_INTO_LABEL = 'Reserve a Switch into this Project';

// The Invite link of a Project, as the owner shares it
export const inviteUrl = (token) => `${window.location.origin}/join/${token}`;

// Where a Project's Topology opens (its Project page, which opens on it), and, with Projects off,
// a user's. The one place that builds these links
export const projectTopologyPath = (projectId) => `/projects/${projectId}`;
export const userTopologyPath = (ownerId) => `/topology/user/${ownerId}`;
