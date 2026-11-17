// Small helpers over the config: limits from the JSON schema, values by path
// ("triage.dpi"), and friendlier wording for the sidecar's validation errors.

import type { FieldError, JsonSchema } from './types'

export interface Limits {
  min?: number
  max?: number
  /** The value must be strictly above min (or below max). */
  exclusiveMin?: boolean
  exclusiveMax?: boolean
  integer: boolean
  nullable: boolean
}

function resolve(root: JsonSchema, node: JsonSchema | undefined): JsonSchema | undefined {
  let current = node
  for (let i = 0; current?.$ref && i < 10; i++) {
    const name = current.$ref.replace(/^#\/\$defs\//, '')
    current = root.$defs?.[name]
  }
  return current
}

/** The schema node at a dotted path, e.g. "extraction.max_tokens_schedule". */
export function schemaAt(root: JsonSchema, path: string): JsonSchema | undefined {
  let node: JsonSchema | undefined = root
  for (const part of path.split('.')) {
    node = resolve(root, node)
    if (node?.anyOf) node = node.anyOf.map((branch) => resolve(root, branch)).find((b) => b?.properties?.[part])
    node = node?.properties?.[part]
  }
  return node
}

export function limitsFor(root: JsonSchema | null, path: string): Limits {
  const limits: Limits = { integer: false, nullable: false }
  if (!root) return limits
  let node = resolve(root, schemaAt(root, path))
  if (node?.anyOf) {
    limits.nullable = node.anyOf.some((branch) => branch.type === 'null')
    node = node.anyOf.find((branch) => branch.type !== 'null')
  }
  if (node?.type === 'array') node = resolve(root, node.items)
  if (!node) return limits
  limits.integer = node.type === 'integer'
  if (node.minimum !== undefined) limits.min = node.minimum
  if (node.exclusiveMinimum !== undefined) {
    limits.min = node.exclusiveMinimum
    limits.exclusiveMin = true
  }
  if (node.maximum !== undefined) limits.max = node.maximum
  if (node.exclusiveMaximum !== undefined) {
    limits.max = node.exclusiveMaximum
    limits.exclusiveMax = true
  }
  return limits
}

export function getPath(target: unknown, path: string): unknown {
  let node = target
  for (const part of path.split('.')) {
    if (node === null || typeof node !== 'object') return undefined
    node = (node as Record<string, unknown>)[part]
  }
  return node
}

export function setPath(target: object, path: string, value: unknown): void {
  const parts = path.split('.')
  let node = target as Record<string, unknown>
  for (const part of parts.slice(0, -1)) {
    if (node[part] === null || typeof node[part] !== 'object') node[part] = {}
    node = node[part] as Record<string, unknown>
  }
  node[parts[parts.length - 1]] = value
}

/** Errors about a field or anything inside it: "triage" matches "triage.dpi"; "inputs" matches "inputs[0]". */
export function errorsUnder(errors: FieldError[], field: string): FieldError[] {
  return errors.filter((e) => e.field === field || e.field.startsWith(`${field}.`) || e.field.startsWith(`${field}[`))
}

const WORDING: [RegExp, (m: RegExpMatchArray) => string][] = [
  [/^Input should be greater than or equal to (\S+)$/, (m) => `Use ${m[1]} or more.`],
  [/^Input should be greater than (\S+)$/, (m) => `Use a number above ${m[1]}.`],
  [/^Input should be less than or equal to (\S+)$/, (m) => `Use ${m[1]} or less.`],
  [/^Input should be less than (\S+)$/, (m) => `Use a number below ${m[1]}.`],
  [/^Input should be a valid integer/, () => 'Use a whole number.'],
  [/^Input should be a valid number/, () => 'Use a number.'],
  [/^Field required$/, () => 'This is required.'],
  [/^max_tokens_schedule needs at least one positive budget$/, () => 'Give at least one limit, each above 0.'],
  [/^Input not found: (.*)$/s, (m) => `This file or folder cannot be found: ${m[1]}`],
  // The guided questions (table_extract/prompts.py, Guide). "Value error, " is pydantic's own prefix
  [/^(?:Value error, )?this answer cannot be empty$/, () => 'Fill this in: it cannot be empty.'],
  [/^(?:Value error, )?add at least one relationship$/, () => 'Add at least one relationship.'],
  [/^(?:Value error, )?every relationship needs a name$/, () => 'Give every relationship a name.'],
  [/^(?:Value error, )?each relationship needs a different name$/, () => 'Each relationship needs a different name.'],
  [/^(?:Value error, )?at most (\d+) extra columns$/, (m) => `You can add up to ${m[1]} columns of your own.`],
  [/^(?:Value error, )?every extra column needs a name$/, () => 'Give every column of your own a name.'],
  [/^(?:Value error, )?each extra column needs a different name$/, () => 'Each column of your own needs a different name.'],
  [
    /^(?:Value error, )?an extra column cannot be named like a standard column or start with an underscore$/,
    () => 'Choose another name: this one is used by a standard column, or starts with an underscore.',
  ],
  // A prompt edited by hand (table_extract/prompts.py, FORMAT_MARKER)
  [
    /^(?:Value error, )?keep the line (.+) exactly once: it marks where the answer format goes$/,
    (m) => `Keep the line ${m[1]} once, where the answer format should go. Rebuild from the answers to start again.`,
  ],
]

/** The sidecar's message, reworded where pydantic's wording is technical. */
export function friendlyMessage(error: FieldError): string {
  if (error.field === 'inputs' && error.type === 'too_short') return 'Add at least one document or folder.'
  if (error.field === 'output_dir' && (error.type === 'path_type' || error.type === 'missing'))
    return 'Choose a folder for the results.'
  for (const [pattern, word] of WORDING) {
    const match = error.message.match(pattern)
    if (match) return word(match)
  }
  return error.message
}
