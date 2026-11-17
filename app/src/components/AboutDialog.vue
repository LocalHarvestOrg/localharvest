<script setup lang="ts">
import { AppIcon } from '@icij/murmur'
import IPhCircleNotch from '~icons/ph/circle-notch'
import { BModal } from 'bootstrap-vue-next'
import { computed, ref, watch } from 'vue'
import { interfaceLibraries, shellLibraries } from 'virtual:credits'

import { ApiError } from '@/api/client'
import type { About, Library } from '@/api/types'
import { APP_NAME, CREDITS } from '@/app'
import { useSidecarStore } from '@/stores/sidecar'

const open = defineModel<boolean>({ required: true })

const sidecar = useSidecarStore()
const about = ref<About | null>(null)
const error = ref<string | null>(null)

// Asked once: what the engine reports does not change while the app is open, except llama.cpp
watch(open, async (shown) => {
  if (!shown || (about.value && !error.value)) return
  error.value = null
  try {
    about.value = await sidecar.api.about()
  } catch (e) {
    // 404: a background service from before GET /about (a frozen sidecar not rebuilt, or one started earlier)
    if (e instanceof ApiError && e.status === 404) error.value = 'The background service is older than this window. Restart the app to see this list.'
    else error.value = e instanceof Error ? e.message : String(e)
  }
})

const downloaded = computed<Library[]>(() => {
  if (!about.value) return []
  const llama = about.value.llama_cpp
  return [
    { name: 'llama.cpp', version: llama.build ?? 'Not installed yet', license: llama.license },
    ...about.value.models.map((m) => ({ name: m.label, version: m.repo, license: m.license })),
  ]
})

const groups = computed(() => [
  { title: 'Interface', libraries: interfaceLibraries },
  { title: 'Desktop app', libraries: shellLibraries },
  { title: 'Processing engine', libraries: about.value?.libraries ?? [] },
  { title: 'Downloaded by the app when needed', libraries: downloaded.value },
])
</script>

<template>
  <b-modal v-model="open" :title="`About ${APP_NAME}`" size="lg" scrollable no-footer>
    <p class="mb-1">
      <span class="fw-semibold">{{ APP_NAME }}</span>, tables from PDF documents.
      <span v-if="sidecar.health" class="text-secondary">Version {{ sidecar.health.version }}</span>
    </p>
    <dl class="about-dialog__credits small mb-4">
      <dt>Editor</dt>
      <dd>{{ CREDITS.editor }}</dd>
      <dt>Project</dt>
      <dd>{{ CREDITS.project }}</dd>
    </dl>

    <h2 class="h5">Libraries</h2>
    <p class="text-secondary small">{{ APP_NAME }} is built with these open-source libraries and models.</p>

    <section v-for="group in groups" :key="group.title" class="mb-4">
      <h3 class="h6 mb-2">{{ group.title }}</h3>
      <div v-if="group.libraries.length === 0" class="small text-secondary">
        <span v-if="error">{{ error }}</span>
        <span v-else-if="!about" class="d-flex align-items-center gap-2"><app-icon :name="IPhCircleNotch" spin /> Loading…</span>
        <span v-else>None</span>
      </div>
      <table v-else class="table table-sm small mb-0 about-dialog__table">
        <thead>
          <tr>
            <th scope="col">Name</th>
            <th scope="col">Version</th>
            <th scope="col">License</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="library in group.libraries" :key="library.name">
            <td>{{ library.name }}</td>
            <td class="text-secondary text-break">{{ library.version ?? 'Unknown' }}</td>
            <td class="text-secondary">{{ library.license ?? 'Unknown' }}</td>
          </tr>
        </tbody>
      </table>
    </section>
  </b-modal>
</template>

<style lang="scss" scoped>
.about-dialog__credits {
  display: grid;
  grid-template-columns: max-content 1fr;
  column-gap: 1rem;

  dd {
    margin: 0;
  }
}

.about-dialog__table {
  table-layout: fixed;

  th:first-child {
    width: 40%;
  }

  th:nth-child(2) {
    width: 35%;
  }
}
</style>
