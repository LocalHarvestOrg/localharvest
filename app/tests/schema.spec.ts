import { describe, expect, it } from 'vitest'

import { errorsUnder, friendlyMessage, getPath, limitsFor, setPath } from '@/api/schema'
import type { FieldError, JsonSchema } from '@/api/types'

import schemaFixture from './fixtures/schema.json'

const schema = schemaFixture as JsonSchema

function error(field: string, message: string, type = 'x'): FieldError {
  return { field, loc: field.split('.'), message, type }
}

describe('limitsFor', () => {
  it('follows $refs to nested limits', () => {
    expect(limitsFor(schema, 'triage.dpi')).toMatchObject({ min: 36, max: 600, integer: true, nullable: false })
  })

  it('reads optional numbers and exclusive minimums', () => {
    expect(limitsFor(schema, 'sampling.top_p')).toMatchObject({ min: 0, exclusiveMin: true, max: 1, nullable: true })
    expect(limitsFor(schema, 'sampling.seed')).toMatchObject({ integer: true, nullable: true })
  })

  it('reads the items of a list', () => {
    expect(limitsFor(schema, 'extraction.max_tokens_schedule').integer).toBe(true)
  })

  it('is empty for unknown paths or without a schema', () => {
    expect(limitsFor(schema, 'nope.nothing')).toEqual({ integer: false, nullable: false })
    expect(limitsFor(null, 'triage.dpi')).toEqual({ integer: false, nullable: false })
  })
})

describe('paths', () => {
  it('gets and sets nested values, list items included', () => {
    const target = { a: { b: 1, list: ['x', 'y'] } }
    expect(getPath(target, 'a.b')).toBe(1)
    expect(getPath(target, 'a.list.1')).toBe('y')
    expect(getPath(target, 'a.missing.deeper')).toBeUndefined()
    setPath(target, 'a.list.0', 'z')
    setPath(target, 'c.d', 2)
    expect(target).toEqual({ a: { b: 1, list: ['z', 'y'] }, c: { d: 2 } })
  })
})

describe('errorsUnder', () => {
  it('matches a field, its children and its list items, not its prefix lookalikes', () => {
    const errors = [error('inputs', 'a'), error('inputs[0]', 'b'), error('triage.dpi', 'c'), error('triage_x', 'd')]
    expect(errorsUnder(errors, 'inputs').map((e) => e.message)).toEqual(['a', 'b'])
    expect(errorsUnder(errors, 'triage').map((e) => e.message)).toEqual(['c'])
  })
})

describe('friendlyMessage', () => {
  it.each([
    ['Input should be greater than or equal to 36', 'Use 36 or more.'],
    ['Input should be less than or equal to 600', 'Use 600 or less.'],
    ['Input should be greater than 0', 'Use a number above 0.'],
    ['Input should be a valid integer, unable to parse string as an integer', 'Use a whole number.'],
    ['Input should be a valid number, unable to parse string as a number', 'Use a number.'],
    ['Input not found: /tmp/x.pdf', 'This file or folder cannot be found: /tmp/x.pdf'],
  ])('rewords %s', (message, expected) => {
    expect(friendlyMessage(error('triage.dpi', message))).toBe(expected)
  })

  it('explains an empty document list and a missing results folder', () => {
    expect(friendlyMessage(error('inputs', 'List should have at least 1 item', 'too_short'))).toBe(
      'Add at least one document or folder.',
    )
    expect(friendlyMessage(error('output_dir', 'Input is not a valid path', 'path_type'))).toBe(
      'Choose a folder for the results.',
    )
  })

  it('keeps messages written for people', () => {
    const message = 'http://example.com is not on this machine.'
    expect(friendlyMessage(error('server', message, 'value_error'))).toBe(message)
  })
})
