// How the UI names a Switch: its model and id, as the API's texts do (api/api/switch_details.py).
// The management IP, console and hardware only come with the Switches the user may work on
// (and every Switch for an admin), so nothing keys or labels a Switch by them.
export const switchLabel = (sw) => `${sw.model} #${sw.id}`;
