// What the window can do on its own computer: native dialogs, drag and drop
// with real paths, opening a folder, keeping the computer awake. All of it
// needs the Tauri shell; in browser dev mode the UI falls back to typed paths.

import { browserTransport, tauriTransport } from '@/api/transports'
import type { Transport } from '@/api/client'

export type DropEvent = { type: 'enter' | 'leave' } | { type: 'drop'; paths: string[] }

export interface Platform {
  readonly kind: 'tauri' | 'browser'
  /** Native dialogs and drag and drop give real paths. */
  readonly hasNativeFiles: boolean
  connect(): Promise<Transport>
  pickDocuments(): Promise<string[]>
  pickDocumentFolders(): Promise<string[]>
  pickOutputFolder(defaultPath?: string): Promise<string | null>
  openFolder(path: string): Promise<void>
  onDrop(handler: (event: DropEvent) => void): Promise<() => void>
  keepAwake(on: boolean): Promise<void>
  documentsFolder(): Promise<string | null>
}

declare global {
  interface Window {
    __TAURI_INTERNALS__?: unknown
  }
}

export function isTauri(): boolean {
  return typeof window !== 'undefined' && window.__TAURI_INTERNALS__ !== undefined
}

interface SidecarInfo {
  url: string
  token: string
}

function asList(selection: string | string[] | null): string[] {
  if (selection === null) return []
  return Array.isArray(selection) ? selection : [selection]
}

const tauriPlatform: Platform = {
  kind: 'tauri',
  hasNativeFiles: true,
  async connect() {
    const { invoke } = await import('@tauri-apps/api/core')
    // Waits until the sidecar has printed its port (or failed to start)
    const info = await invoke<SidecarInfo>('sidecar_info')
    return tauriTransport(info.url, info.token)
  },
  async pickDocuments() {
    const { open } = await import('@tauri-apps/plugin-dialog')
    const selection = await open({
      title: 'Choose PDF documents',
      multiple: true,
      filters: [{ name: 'PDF documents', extensions: ['pdf', 'PDF'] }],
    })
    return asList(selection)
  },
  async pickDocumentFolders() {
    const { open } = await import('@tauri-apps/plugin-dialog')
    return asList(await open({ title: 'Choose folders of PDF documents', directory: true, multiple: true }))
  },
  async pickOutputFolder(defaultPath) {
    const { open } = await import('@tauri-apps/plugin-dialog')
    const selection = await open({
      title: 'Choose where to save the results',
      directory: true,
      multiple: false,
      canCreateDirectories: true,
      defaultPath,
    })
    return asList(selection)[0] ?? null
  },
  async openFolder(path) {
    const { invoke } = await import('@tauri-apps/api/core')
    await invoke('open_folder', { path })
  },
  async onDrop(handler) {
    const { getCurrentWebview } = await import('@tauri-apps/api/webview')
    return getCurrentWebview().onDragDropEvent((event) => {
      const payload = event.payload
      if (payload.type === 'drop') handler({ type: 'drop', paths: payload.paths })
      else if (payload.type === 'enter') handler({ type: 'enter' })
      else if (payload.type === 'leave') handler({ type: 'leave' })
    })
  },
  async keepAwake(on) {
    const { invoke } = await import('@tauri-apps/api/core')
    await invoke('keep_awake', { on })
  },
  async documentsFolder() {
    const { documentDir } = await import('@tauri-apps/api/path')
    return documentDir().catch(() => null)
  },
}

const browserPlatform: Platform = {
  kind: 'browser',
  hasNativeFiles: false,
  async connect() {
    // The dev sidecar's token (README: TABLE_EXTRACT_SIDECAR_TOKEN=dev-token)
    return browserTransport(import.meta.env.VITE_SIDECAR_TOKEN || 'dev-token')
  },
  async pickDocuments() {
    return []
  },
  async pickDocumentFolders() {
    return []
  },
  async pickOutputFolder() {
    return null
  },
  async openFolder() {
    throw new Error('Opening folders needs the desktop app.')
  },
  async onDrop() {
    return () => undefined
  },
  async keepAwake() {},
  async documentsFolder() {
    return null
  },
}

export const platform: Platform = isTauri() ? tauriPlatform : browserPlatform
