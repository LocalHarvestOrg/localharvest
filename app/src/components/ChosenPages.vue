<script setup lang="ts">
import { AppIcon, ButtonIcon } from '@icij/murmur'
import IPhFilePdf from '~icons/ph/file-pdf'
import IPhTrash from '~icons/ph/trash'
import { computed, nextTick, ref, useId, watch } from 'vue'

import { friendlyMessage } from '@/api/schema'
import { useConfigStore } from '@/stores/config'

import FieldMessages from './FieldMessages.vue'

// Pages chosen by hand for some documents, kept in triage.pages by file name:
// only those pages are extracted, and the model triage skips the document.

const config = useConfigStore()
const id = useId()
const adding = ref('')
const inputs = new Map<string, HTMLInputElement>()

const triageOn = computed(() => !!config.config?.triage.enabled)
const chosen = computed(() => Object.entries(config.config?.triage.pages ?? {}))

function fileName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() ?? path
}

// The sidecar lists the PDFs only once the settings are valid: while a page list
// has a typo, keep the documents found last
const pdfs = ref<string[]>([])
watch(
  () => config.validation,
  (validation) => {
    const hasInputs = !!config.config?.inputs.length
    if (validation && (validation.pdfs.length || !hasInputs || config.errorsFor('inputs').length)) {
      pdfs.value = validation.pdfs
    }
  },
  { immediate: true },
)

const pathOf = computed(() => new Map(pdfs.value.map((path) => [fileName(path), path])))
const available = computed(() =>
  [...pathOf.value.keys()].filter((name) => !(name in (config.config?.triage.pages ?? {}))),
)

function isAbout(loc: (string | number)[], name?: string): boolean {
  return loc[0] === 'triage' && loc[1] === 'pages' && (name === undefined ? loc.length === 2 : loc[2] === name)
}

function messagesFor(name: string): string[] {
  return config.errors.filter((e) => isAbout(e.loc, name)).map(friendlyMessage)
}

const otherMessages = computed(() => config.errors.filter((e) => isAbout(e.loc)).map(friendlyMessage))

async function add() {
  const name = adding.value
  adding.value = ''
  if (!name) return
  config.setChosenPages(name, '')
  await nextTick()
  inputs.get(name)?.focus()
}

function keepInput(name: string, element: unknown) {
  if (element instanceof HTMLInputElement) inputs.set(name, element)
  else inputs.delete(name)
}
</script>

<template>
  <div class="chosen-pages border-top pt-3 mt-3">
    <h3 class="h6 mb-1">I know the pages of some documents</h3>
    <p class="small text-secondary mb-3">
      If you already know which pages of a document hold the tables, give them here: only those pages are
      read{{ triageOn ? ', and the model does not look for them in that document' : '' }}. For the other
      documents, {{ triageOn ? 'the model finds the pages' : 'every page is read' }}.
    </p>

    <ul v-if="chosen.length" class="list-group mb-3">
      <li v-for="[name, pages] in chosen" :key="name" class="list-group-item">
        <div class="d-flex flex-wrap align-items-center gap-2">
          <label
            :for="`${id}-${name}`"
            class="chosen-pages__name d-flex align-items-center gap-1 mb-0 fw-medium"
            :title="pathOf.get(name) ?? name"
          >
            <app-icon :name="IPhFilePdf" class="text-secondary flex-shrink-0" />
            <span class="text-truncate">{{ name }}</span>
          </label>
          <input
            :id="`${id}-${name}`"
            :ref="(element) => keepInput(name, element)"
            :value="pages"
            type="text"
            class="chosen-pages__input form-control form-control-sm"
            :class="{ 'is-invalid': messagesFor(name).length }"
            placeholder="For example 3, 7-12"
            spellcheck="false"
            autocomplete="off"
            :aria-describedby="`${id}-help`"
            @input="config.setChosenPages(name, ($event.target as HTMLInputElement).value)"
          />
          <button-icon
            variant="link"
            class="text-secondary p-1"
            :icon-left="IPhTrash"
            hide-label
            :label="`Let ${triageOn ? 'the model pick the pages of' : 'every page be read in'} ${name}`"
            @click="config.removeChosenPages(name)"
          />
        </div>
        <field-messages :messages="messagesFor(name)" />
      </li>
    </ul>

    <field-messages :messages="otherMessages" />

    <select
      v-if="available.length"
      v-model="adding"
      class="form-select chosen-pages__add"
      aria-label="Choose the pages of a document"
      @change="add"
    >
      <option value="" disabled>{{ chosen.length ? 'Choose pages in another document…' : 'Choose pages in a document…' }}</option>
      <option v-for="name in available" :key="name" :value="name">{{ name }}</option>
    </select>
    <p v-else-if="!pdfs.length" class="small text-secondary mb-0">
      Add documents first, above.
    </p>

    <div v-if="pdfs.length" :id="`${id}-help`" class="form-text">
      Count pages from the start of the PDF file, as your PDF viewer shows them (“page 47 of 120”), not the
      numbers printed on the pages. Separate pages with commas and give ranges with a dash: 3, 7-12.
    </div>
  </div>
</template>

<style scoped lang="scss">
.chosen-pages {
  &__name {
    flex: 1 1 12rem;
    min-width: 0;
  }

  &__input {
    flex: 1 1 10rem;
    max-width: 18rem;
  }

  &__add {
    max-width: 28rem;
  }
}
</style>
