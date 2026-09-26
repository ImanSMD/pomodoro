import { describe, expect, it } from 'vitest'

import type { Category } from '@/api/types'

import { archivedCategories, categoryColor, liveCategories } from './api'

function category(id: string, archived: boolean, color: string | null = '#2a8c84'): Category {
  return {
    id,
    name: id,
    color,
    icon: null,
    position: 0,
    archived_at: archived ? '2026-09-01T00:00:00Z' : null,
    created_at: '2026-08-01T00:00:00Z',
  }
}

describe('category name resolution', () => {
  const all = [category('live', false), category('shelved', true)]

  it('offers only live categories in a picker', () => {
    expect(liveCategories(all).map((c) => c.id)).toEqual(['live'])
  })

  it('keeps archived ones available to name the tasks that point at them', () => {
    expect(archivedCategories(all).map((c) => c.id)).toEqual(['shelved'])
  })
})

describe('categoryColor()', () => {
  it('passes a plain hex through and falls back for anything else', () => {
    expect(categoryColor(category('a', false, '#2A8C84'))).toBe('#2A8C84')
    expect(categoryColor(category('a', false, null))).toBe('#66757a')
    // The column is free text; nothing but a hex reaches a style attribute.
    expect(categoryColor(category('a', false, 'red; background:url(x)'))).toBe('#66757a')
  })
})
