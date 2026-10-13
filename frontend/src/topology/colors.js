// The colours the Topology draws with outside of CSS (cytoscape, the cards' SVG), from the same
// palettes as the Tailwind tokens (tailwind.config.js): primary is teal, warning is amber,
// ghost is violet. Red is for destructive actions only, never a state.
import colors from 'tailwindcss/colors';

export const COLORS = {
  primary: colors.teal,
  warning: colors.amber,
  gray: colors.gray,
  ghost: colors.violet,
};

export const FONT = "ui-sans-serif, system-ui, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif";
export const MONO = "ui-monospace, 'Cascadia Mono', Consolas, Menlo, monospace";
