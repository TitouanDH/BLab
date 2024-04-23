import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
  plugins: [vue()],
  server: {
    host: true,
    port: 5173 // Adjust the port number as needed
  },
  build: {
    outDir: 'dist' // Adjust the output directory as needed
  }
});