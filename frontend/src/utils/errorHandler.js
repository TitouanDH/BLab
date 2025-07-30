/**
 * Global error handling utilities
 */

/**
 * Standardized error message extraction from API responses
 * @param {Error} error - The error object from axios or other sources
 * @param {string} fallbackMessage - Default message if no specific error is found
 * @returns {string} - User-friendly error message
 */
export function getErrorMessage(error, fallbackMessage = 'An unexpected error occurred. Please try again.') {
  if (error?.response?.data?.detail) {
    return error.response.data.detail;
  }
  
  if (error?.response?.data?.error) {
    return error.response.data.error;
  }
  
  if (error?.response?.data?.warning) {
    return error.response.data.warning;
  }
  
  if (error?.message) {
    return error.message;
  }
  
  return fallbackMessage;
}

/**
 * Handle common API errors with standardized messages
 * @param {Error} error - The error object
 * @param {string} context - Context of where the error occurred (e.g., 'login', 'reservation')
 * @returns {string} - Formatted error message
 */
export function handleApiError(error, context = '') {
  const statusCode = error?.response?.status;
  
  switch (statusCode) {
    case 400:
      return getErrorMessage(error, 'Invalid request. Please check your input and try again.');
    case 401:
      return 'Authentication failed. Please log in again.';
    case 403:
      return 'You do not have permission to perform this action.';
    case 404:
      return 'The requested resource was not found.';
    case 409:
      return getErrorMessage(error, 'Conflict detected. The operation could not be completed.');
    case 422:
      return getErrorMessage(error, 'The request could not be processed. Please check your input.');
    case 500:
      return 'Server error. Please try again later or contact support.';
    default:
      return getErrorMessage(error, `Failed to ${context}. Please try again.`);
  }
}

/**
 * Log errors consistently
 * @param {Error} error - The error object
 * @param {string} context - Context of where the error occurred
 */
export function logError(error, context = '') {
  console.error(`[${context}] Error:`, {
    message: error?.message,
    status: error?.response?.status,
    data: error?.response?.data,
    stack: error?.stack
  });
}
