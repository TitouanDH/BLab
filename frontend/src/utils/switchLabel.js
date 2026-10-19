// The mngt_IP of a Switch BLab can't reach, such as some Tools (api/api/models.py)
export const NO_MANAGEMENT_IP = 'Not available';

// Whether a mngt_IP is a real address
export const hasManagementIP = (ip) => !!ip && ip !== NO_MANAGEMENT_IP;

// How the UI names a Switch: its model and management IP, "OS6860E-24 10.0.0.1". Every user
// gets every Switch's IP; a Switch without one (some Tools, or a caller that only knows the
// model) is named by its model alone. Lists still key Switches by their id.
export const switchLabel = (sw) => (hasManagementIP(sw.mngt_IP) ? `${sw.model} ${sw.mngt_IP}` : sw.model);

// A model's family, what the Reservation page groups models under: OS6860E-P48 -> OS6860E,
// OS9907 -> OS9907
export const familyOf = (model) => model.split('-')[0];
