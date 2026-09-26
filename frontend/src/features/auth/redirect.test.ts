import { describe, expect, it } from 'vitest'

import { safeRedirect } from './redirect'

describe('safeRedirect()', () => {
  it('keeps in-app paths', () => {
    expect(safeRedirect('/c/123?x=1')).toBe('/c/123?x=1')
  })

  it('refuses anything a browser would read as another origin', () => {
    for (const from of ['//evil.example', '/\\evil.example', '/\\/evil.example', 'https://evil.example', 'evil', '']) {
      expect(safeRedirect(from), from).toBe('/')
    }
    expect(safeRedirect(undefined)).toBe('/')
  })
})
