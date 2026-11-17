import { defineStore } from 'pinia'
import { computed, ref, shallowRef } from 'vue'

import { ApiError, SidecarClient } from '@/api/client'
import type { Health } from '@/api/types'
import { platform } from '@/platform'

/** The connection to the sidecar: every other store goes through `api`. */
export const useSidecarStore = defineStore('sidecar', () => {
  const client = shallowRef<SidecarClient | null>(null)
  const health = ref<Health | null>(null)
  const error = ref<string | null>(null)
  const connecting = ref(false)

  const ready = computed(() => client.value !== null && health.value !== null)

  const api = computed(() => {
    if (!client.value) throw new ApiError(0, 'unreachable', 'The app is not connected to its background service.')
    return client.value
  })

  async function connect(): Promise<boolean> {
    connecting.value = true
    error.value = null
    try {
      const candidate = client.value ?? new SidecarClient(await platform.connect())
      health.value = await candidate.health()
      client.value = candidate
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      return false
    } finally {
      connecting.value = false
    }
  }

  return { client, health, error, connecting, ready, api, connect }
})
