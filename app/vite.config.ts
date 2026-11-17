import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import Icons from 'unplugin-icons/vite'
import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'

import { credits } from './credits.ts'

// Browser dev mode talks to a sidecar started by hand on SIDECAR_PORT (see the
// README, "Running the app in development"). The proxy keeps the requests
// same-origin, so the sidecar needs no CORS headers. In Tauri the window calls
// the sidecar through tauri-plugin-http instead, and this proxy is unused.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const sidecarPort = env.SIDECAR_PORT || '8765'
  return {
    plugins: [vue(), Icons({ compiler: 'vue3', autoInstall: false }), credits()],
    clearScreen: false,
    resolve: {
      alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
    },
    css: {
      preprocessorOptions: {
        scss: {
          // Bootstrap 5.3 still uses @import and the global Sass functions
          quietDeps: true,
          silenceDeprecations: ['import', 'global-builtin', 'color-functions', 'if-function'],
        },
      },
    },
    server: {
      port: 1420,
      strictPort: true,
      proxy: {
        '/sidecar': {
          target: `http://127.0.0.1:${sidecarPort}`,
          changeOrigin: true, // the sidecar only answers Host: 127.0.0.1:<its port>
          rewrite: (path) => path.replace(/^\/sidecar/, ''),
        },
      },
    },
    envPrefix: ['VITE_', 'TAURI_ENV_'],
    build: {
      target: 'safari15',
      sourcemap: false,
    },
    test: {
      environment: 'happy-dom',
      include: ['tests/**/*.spec.ts', 'src/**/*.test.ts'],
      // Murmur's components import their own CSS files, which Node cannot load: let Vite process them
      server: { deps: { inline: ['@icij/murmur'] } },
    },
  }
})
