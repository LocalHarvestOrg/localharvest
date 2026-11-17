import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

import { cargoLibraries, npmLibraries } from '../credits'

describe('credits', () => {
  it('lists the installed npm dependencies, without the dev ones', () => {
    const libraries = npmLibraries(process.cwd())
    const vue = libraries.find((l) => l.name === 'vue')
    const installed = JSON.parse(readFileSync('node_modules/vue/package.json', 'utf-8'))
    expect(vue).toEqual({ name: 'vue', version: installed.version, license: 'MIT' })
    expect(libraries.find((l) => l.name === 'vite')).toBeUndefined()
  })

  it('lists the Rust crates the shell depends on, without the build ones', () => {
    const libraries = cargoLibraries(process.cwd())
    expect(libraries.find((l) => l.name === 'tauri')?.license).toBeTruthy()
    expect(libraries.find((l) => l.name === 'tauri-build')).toBeUndefined()
  })
})
