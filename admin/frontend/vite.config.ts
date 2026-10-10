import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => ({
  plugins: [tailwindcss(), react()],
  server: { host: "127.0.0.1", port: 5174, strictPort: true, proxy: { "/api": { target: "http://127.0.0.1:8001", changeOrigin: false } } },
  build: { rollupOptions: { input: mode === "production" ? ["index.html"] : ["index.html", "masking-validation.html"] } },
  test: {
    environment: 'jsdom',
    environmentOptions: {
      jsdom: {
        url: 'http://localhost/',
      },
    },
    setupFiles: ['./src/test/setup.ts'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json-summary', 'lcov'],
      reportsDirectory: './coverage',
      thresholds: {
        statements: 55,
        branches: 50,
        functions: 40,
        lines: 60,
      },
    },
  },
}))
