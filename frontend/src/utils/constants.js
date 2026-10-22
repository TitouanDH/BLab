/**
 * Application constants
 */

// API Constants
export const API_ENDPOINTS = {
  LOGIN: 'login/',
  LOGOUT: 'logout/',
  SIGNUP: 'signup/',
  ACCOUNT: 'account/',
  SWITCH_PASSWORD: 'switch_password/',
  LIST_SWITCH: 'list_switch/',
  LIST_RESERVATION: 'list_reservation/',
  LIST_USER: 'list_user/',
  LIST_PORT: 'list_port/',
  RESERVE: 'reserve/',
  RELEASE: 'release/',
  RENEW: 'renew/',
  CONNECT: 'connect/',
  DISCONNECT: 'disconnect/',
  SHARE_TOPOLOGY: 'share_topology/',
  LIST_SHARED_TOPOLOGIES: 'list_shared_topologies/',
  UNSHARE_TOPOLOGY: 'unshare_topology/',
  TOPOLOGY: 'topology/',
  LAB_STATUS: 'lab_status/',
  CONTRIBUTE_CABLE: 'contribute/cable/',
  CONTRIBUTE_SWITCH: 'contribute/switch/',
  RELEASE_CHECK: 'release_check/',
  RECHECK: 'recheck/',
  MY_LAB: 'my_lab/',
  PROJECTS: 'projects/',  // with Projects on (BLAB_PROJECTS); 404 when off
  JOIN: 'join/'
}

// Reservation limits; the server enforces them (api/api/reservations.py)
export const MAX_RESERVATION_DAYS = 14;
export const DEFAULT_RESERVATION_DAYS = 7;  // what one click on Reserve gives
export const RENEWAL_DAYS = 7;
export const MAX_RENEWALS = 2;

// Local Storage Keys
export const STORAGE_KEYS = {
  TOKEN: 'token',
  USER: 'user',
  IS_STAFF: 'is_staff',
  EMAIL: 'email',
  SWITCH_PASSWORD: 'switch_password',  // 'done' once BLab said none is needed (chosen, or BLAB_PROJECTS off)
  PROJECTS: 'projects'  // 'on' once BLab answered projects/, 'off' once it said it has none
}
