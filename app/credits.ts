// The About panel's lists of the interface's and the desktop shell's libraries,
// as the virtual module "virtual:credits". Read when the app is built, so they
// always name the versions in it: the npm dependencies from node_modules, and
// the Rust crates from `cargo metadata`. The engine reports its own (GET /about).

import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import type { Plugin } from 'vite'

import type { Library } from './src/api/types.ts'

const ID = 'virtual:credits'
const RESOLVED = `\0${ID}`
const readJson = (path: string) => JSON.parse(readFileSync(path, 'utf-8'))

/** package.json's dependencies (not devDependencies, which are not in the app), as installed. */
export function npmLibraries(root: string): Library[] {
  const names = Object.keys(readJson(join(root, 'package.json')).dependencies ?? {})
  return names.map((name) => {
    const pkg = readJson(join(root, 'node_modules', name, 'package.json'))
    const license = typeof pkg.license === 'string' ? pkg.license : (pkg.license?.type ?? null)
    return { name, version: pkg.version ?? null, license }
  })
}

interface CargoPackage {
  id: string
  name: string
  version: string
  license: string | null
}

interface CargoMetadata {
  packages: CargoPackage[]
  resolve: { root: string; nodes: { id: string; deps: { pkg: string; dep_kinds: { kind: string | null }[] }[] }[] }
}

/** Cargo.toml's [dependencies] (not build-dependencies), as resolved by Cargo.lock. */
export function cargoLibraries(root: string): Library[] {
  const output = execFileSync(
    'cargo',
    ['metadata', '--format-version', '1', '--offline', '--locked', '--manifest-path', join(root, 'src-tauri', 'Cargo.toml')],
    { encoding: 'utf-8', maxBuffer: 256 * 1024 * 1024, stdio: ['ignore', 'pipe', 'pipe'] },
  )
  const meta: CargoMetadata = JSON.parse(output)
  const packages = new Map(meta.packages.map((p) => [p.id, p]))
  const node = meta.resolve.nodes.find((n) => n.id === meta.resolve.root)
  return (node?.deps ?? [])
    .filter((dep) => dep.dep_kinds.some((k) => k.kind === null))
    .map((dep) => packages.get(dep.pkg)!)
    .map(({ name, version, license }) => ({ name, version, license }))
    .sort((a, b) => a.name.localeCompare(b.name))
}

export function credits(): Plugin {
  let building = false
  let root = ''
  return {
    name: 'localharvest-credits',
    configResolved(config) {
      building = config.command === 'build'
      root = config.root
    },
    resolveId(id) {
      return id === ID ? RESOLVED : undefined
    },
    load(id) {
      if (id !== RESOLVED) return undefined
      let shell: Library[] = []
      try {
        shell = cargoLibraries(root)
      } catch (error) {
        // A release must list them; browser dev mode can do without Rust
        if (building) throw error
        this.warn(`The About panel will not list the Rust crates: ${error instanceof Error ? error.message : error}`)
      }
      return [
        `export const interfaceLibraries = ${JSON.stringify(npmLibraries(root))}`,
        `export const shellLibraries = ${JSON.stringify(shell)}`,
      ].join('\n')
    },
  }
}
