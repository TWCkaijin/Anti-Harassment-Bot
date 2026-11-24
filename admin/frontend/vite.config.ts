import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import type { Plugin } from 'vite'
import { TINY_ASSET_PATH, TINY_REVISION } from './src/masking-lab/config'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const tinyAssets = { 'model.onnx': 'application/octet-stream', 'vocab.txt': 'text/plain; charset=utf-8' }
const assetFile = (name: string) => fileURLToPath(new URL(`./.masking-tiny/${TINY_REVISION}/${name}`, import.meta.url))

// The Tiny NER weights are GPL-3.0 (see THIRD_PARTY_NOTICES.md). Hosting them
// distributes them, so a Preview build only includes them when explicitly requested.
const bundleTinyAssets = process.env.MASKING_TINY_BUNDLE === '1'

function serveTinyAssets(previewBuild: boolean): Plugin {
  return {
    name: 'masking-tiny-assets',
    configureServer(server) {
      server.middlewares.use(async (request, response, next) => {
        const path = request.url?.split('?')[0];
        const file = Object.keys(tinyAssets).find(name => path === `/${TINY_ASSET_PATH}${name}`) as keyof typeof tinyAssets | undefined;
        if (request.method !== 'GET' || !file) return next();
        try {
          const data = await readFile(assetFile(file));
          response.setHeader('Content-Type', tinyAssets[file]);
          response.end(data);
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code !== 'ENOENT') return next(error);
          response.statusCode = 404;
          response.end();
        }
      });
    },
    async generateBundle() {
      if (!previewBuild || !bundleTinyAssets) return;
      // Opting in without prepared weights is a mistake, not a silent no-op.
      const files = await Promise.all(Object.keys(tinyAssets).map(async name => ({ name, source: await readFile(assetFile(name)) })));
      for (const { name, source } of files) this.emitFile({ type: 'asset', fileName: TINY_ASSET_PATH + name, source });
    },
  };
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => ({
  plugins: [tailwindcss(), react(), serveTinyAssets(mode === "preview")],
  optimizeDeps: { include: ["onnxruntime-web/wasm"] },
  server: { host: "127.0.0.1", port: 5174, strictPort: true, proxy: { "/api": { target: "http://127.0.0.1:8001", changeOrigin: false } } },
  build: { rollupOptions: { input: mode === "preview" ? ["index.html", "masking-validation.html"] : "index.html" } },
  test: {
    environment: 'jsdom',
    environmentOptions: {
      jsdom: {
        url: 'http://localhost/',
      },
    },
    setupFiles: ['../../shared/frontend/src/test/setup.ts'],
    coverage: {
      provider: 'v8' as const,
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
