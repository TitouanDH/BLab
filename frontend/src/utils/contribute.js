// A Quarantine for an Unwanted cable (its reason as api/api/inspection.py words it): the cable may
// stay, if it goes to a Backbone as a new Port (Contribute a cable, #58)
export const hasUnwantedCable = (reasons = []) => reasons.some(reason => reason.startsWith('Unwanted cable'));
