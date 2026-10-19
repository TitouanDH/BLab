// How the UI names a Switch: its model and id, as the API's texts do (api/api/switch_details.py).
// The management IP, console and hardware only come with the Switches the user may work on
// (and every Switch for an admin), so nothing keys or labels a Switch by them.
export const switchLabel = (sw) => `${sw.model} #${sw.id}`;

// A model's family, what the Reservation page groups models under: OS6860E-P48 -> OS6860E,
// OS9907 -> OS9907
export const familyOf = (model) => model.split('-')[0];
