// What the guided questions know about the result columns. The list and its order are fixed in
// table_extract/schema.py (BUILTIN_FIELDS, REQUIRED_FIELDS, OPTIONAL_FIELDS, CUSTOM_FIELDS): keep them in step.

import type { GuideExampleRow } from '@/api/types'

/** The standard columns that can be switched off, in the order of the results. */
export const OPTIONAL_COLUMNS = [
  'jurisdiction',
  'relation_address',
  'relation_country',
  'relation_nationality',
  'relation_type',
]

/** The keys of the columns of the user's own: the first custom column is custom_1. */
export const CUSTOM_KEYS = ['custom_1', 'custom_2', 'custom_3']
export const MAX_CUSTOM_COLUMNS = CUSTOM_KEYS.length

/** What each standard column is called in the questions. The results file uses the column's own name. */
export const COLUMN_LABELS: Record<string, string> = {
  relation_name: 'Name',
  relation_address: 'Address',
  relation_country: 'Country',
  relation_nationality: 'Nationality',
  relation_type: 'Person or company',
  relationship: 'Relationship',
  jurisdiction: 'Jurisdiction of the main entity',
}

/** The columns of an example row, in the order the model answers them. NOTES is left out: it stays empty. */
export const EXAMPLE_COLUMNS = [
  'relation_name',
  'relation_address',
  'relation_country',
  'relation_nationality',
  'relation_type',
  'relationship',
  ...CUSTOM_KEYS,
]

/** A row with every column empty. */
export function emptyExampleRow(): GuideExampleRow {
  return Object.fromEntries([...EXAMPLE_COLUMNS, 'NOTES'].map((key) => [key, null]))
}

/**
 * The columns of the example for these answers: the ones always asked for, the standard ones that are on,
 * and one per column of the user's own.
 */
export function exampleColumns(columns: string[], customCount: number): string[] {
  return EXAMPLE_COLUMNS.filter((key) => {
    const custom = CUSTOM_KEYS.indexOf(key)
    if (custom >= 0) return custom < customCount
    return key === 'relation_name' || key === 'relationship' || columns.includes(key)
  })
}

/**
 * The standard columns that are on, in the order of the results. The answer is always written in this order,
 * so switching a column off and on again gives back exactly the default.
 */
export function orderedColumns(on: Iterable<string>): string[] {
  const set = new Set(on)
  return OPTIONAL_COLUMNS.filter((key) => set.has(key))
}

/**
 * An example row once the custom column at index is gone: the cells of the columns after it move up
 * (custom_3 becomes custom_2), so each stays with its column.
 */
export function withoutCustomColumn(row: GuideExampleRow, index: number): GuideExampleRow {
  const next = { ...row }
  for (let i = index; i < CUSTOM_KEYS.length; i++) {
    const following = CUSTOM_KEYS[i + 1]
    if (following !== undefined && following in next) next[CUSTOM_KEYS[i]] = next[following]
    else delete next[CUSTOM_KEYS[i]]
  }
  return next
}
