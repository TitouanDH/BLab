/**
 * Application constants
 */

// API Constants
export const API_ENDPOINTS = {
  LOGIN: 'login/',
  LOGOUT: 'logout/',
  SIGNUP: 'signup/',
  ACCOUNT: 'account/',
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
  RELEASE_CHECK: 'release_check/',
  RECHECK: 'recheck/',
  SWITCH_ACCOUNTS: 'switch_accounts/'
}

// Reservation limits; the server enforces them (api/api/reservations.py)
export const MAX_RESERVATION_DAYS = 14;
export const RENEWAL_DAYS = 7;
export const MAX_RENEWALS = 2;

// Local Storage Keys
export const STORAGE_KEYS = {
  TOKEN: 'token',
  USER: 'user',
  IS_STAFF: 'is_staff',
  EMAIL: 'email'
}
