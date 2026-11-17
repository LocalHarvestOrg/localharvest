// How requests reach the sidecar. It sends no CORS headers on purpose (see
// the README, "Desktop app sidecar"), so the window never calls it with its own
// cross-origin fetch:
//
// - browser dev mode: same-origin requests to Vite's dev server, which proxies
//   /sidecar to the sidecar started by hand (vite.config.ts);
// - Tauri: tauri-plugin-http, whose requests are made by the Rust side.

import type { Transport } from './client'

function withToken(init: RequestInit, token: string): RequestInit {
  const headers = new Headers(init.headers)
  headers.set('Authorization', `Bearer ${token}`)
  return { ...init, headers }
}

export function browserTransport(token: string, base = '/sidecar', fetcher: typeof fetch = fetch): Transport {
  return {
    request: (path, init = {}) => fetcher(`${base}${path}`, withToken(init, token)),
  }
}

export function tauriTransport(url: string, token: string): Transport {
  const base = url.replace(/\/$/, '')
  let tauriFetch: Promise<typeof fetch> | null = null
  return {
    async request(path, init = {}) {
      tauriFetch ??= import('@tauri-apps/plugin-http').then((module) => module.fetch as typeof fetch)
      return (await tauriFetch)(`${base}${path}`, withToken(init, token))
    },
  }
}
