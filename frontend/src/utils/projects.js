// Projects (CONTEXT.md), where BLab has them on (BLAB_PROJECTS). BLab says so itself: projects/
// answers 404 while they are off. What it last said is kept in session.projects, for the navbar.
import { session } from '../auth.js';
import { projectService } from './apiService.js';
import { STORAGE_KEYS } from './constants.js';

function remember(enabled) {
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
    remember(false);
    return { enabled: false, projects: [], active: [], lastUsed: null, result: { ...result, success: true } };
  }
  if (!result.success) return { enabled: null, projects: [], active: [], lastUsed: null, result };
  remember(true);
  const projects = result.data.projects || [];
  return { enabled: true, projects, active: projects.filter(p => !p.archived_at), lastUsed: result.data.last_used, result };
}

// The Invite link of a Project, as the owner shares it
export const inviteUrl = (token) => `${window.location.origin}/join/${token}`;
