import { defineConfig } from 'vite';

export default defineConfig({
  // during development, proxy API calls to Flask
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:5000',
        changeOrigin: true,
        rewrite: path => path.replace(/^\/api/, '')
      }
    }
  },
  // build into `dist/`, nesting JS/CSS under `static/`
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    assetsDir: 'static'
  }
});
