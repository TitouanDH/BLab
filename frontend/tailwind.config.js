// Tailwind v3. Pages use the colour tokens below, never a raw palette colour for these roles
// (see #27): one primary (teal); danger (red) only for destructive actions such as Release
// and disconnect; success (green) and warning (amber) only for status.
import colors from 'tailwindcss/colors';

export default {
  content: ['./index.html', './src/**/*.{vue,js}'],
  theme: {
    extend: {
      colors: {
        primary: colors.teal,
        danger: colors.red,
        success: colors.green,
        warning: colors.amber,
      },
    },
  },
  plugins: [],
};
