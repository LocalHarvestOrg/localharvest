<script setup lang="ts">
import { AppIcon } from '@icij/murmur'
import IPhCaretRight from '~icons/ph/caret-right-bold'
import { type Component, useId } from 'vue'

// A closed card on the Settings screen: its title and one line say what is
// inside, a click opens it. The content is only rendered while open.
// `featured` sets a card apart from the others (tinted, with an icon before the title).

defineProps<{ title: string; description?: string; badge?: string; featured?: boolean; icon?: Component }>()
const open = defineModel<boolean>('open', { default: false })
const id = useId()
</script>

<template>
  <section
    class="collapsible-section card mb-3"
    :class="{ 'collapsible-section--open': open, 'collapsible-section--featured': featured }"
  >
    <h2 class="h6 mb-0">
      <button
        type="button"
        class="collapsible-section__toggle"
        :aria-expanded="open"
        :aria-controls="id"
        @click="open = !open"
      >
        <app-icon :name="IPhCaretRight" class="collapsible-section__caret text-secondary flex-shrink-0" />
        <app-icon v-if="icon" :name="icon" size="lg" class="collapsible-section__icon flex-shrink-0" />
        <span class="d-flex flex-column flex-grow-1 min-width-0">
          <span class="fw-semibold">
            {{ title }}
            <span v-if="badge" class="badge text-bg-warning ms-2 align-middle fw-medium">{{ badge }}</span>
          </span>
          <span v-if="description" class="small text-secondary fw-normal">{{ description }}</span>
        </span>
      </button>
    </h2>
    <div v-if="open" :id="id" class="collapsible-section__body">
      <slot />
    </div>
  </section>
</template>

<style scoped lang="scss">
.collapsible-section {
  &__toggle {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    width: 100%;
    border: 0;
    background: transparent;
    text-align: left;
    padding: 1rem 1.5rem;
    color: var(--bs-body-color);
    border-radius: var(--bs-card-inner-border-radius);

    &:hover {
      background: var(--bs-tertiary-bg);
    }
  }

  &__caret {
    transition: transform 0.15s;
  }

  &--open &__caret {
    transform: rotate(90deg);
  }

  &__body {
    padding: 0.5rem 1.5rem 1.5rem 3.25rem;
  }

  &--featured {
    border-color: var(--bs-action-border-subtle);
    border-left: 4px solid var(--bs-action);
    background: var(--bs-action-bg-subtle);
    margin-bottom: 1.5rem !important;
  }

  &--featured &__toggle {
    padding-top: 1.25rem;
    padding-bottom: 1.25rem;

    &:hover {
      background: rgba(var(--bs-action-rgb), 0.06);
    }
  }

  &__icon {
    color: var(--bs-action-text-emphasis);
  }
}

.min-width-0 {
  min-width: 0;
}
</style>
