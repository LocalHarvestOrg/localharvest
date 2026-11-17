/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Browser dev mode: the token the hand-started sidecar uses (default dev-token). */
  readonly VITE_SIDECAR_TOKEN?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

/** The interface's and the desktop shell's direct dependencies, read at build time (credits.ts). */
declare module 'virtual:credits' {
  import type { Library } from '@/api/types'
  export const interfaceLibraries: Library[]
  export const shellLibraries: Library[]
}
