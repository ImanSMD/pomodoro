import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo } from 'react'

import { api } from '@/api/client'
import type { Category } from '@/api/types'
import { tasksKey } from '@/features/tasks/api'
import { removeById, upsertById } from '@/lib/listCache'

export const categoriesKey = ['categories'] as const

/**
 * Every category, archived included. A task may point at an archived one —
 * archiving keeps the grouping, unlike delete which detaches — so loading
 * only the live list would leave category_ids nothing can name. Archived
 * ones are shown muted and never offered in a picker; see liveCategories().
 */
export function useCategories() {
  return useQuery({
    queryKey: categoriesKey,
    queryFn: ({ signal }) => api<Category[]>('/api/categories?include_archived=true', { signal }),
  })
}

export function liveCategories(categories: Category[] | undefined): Category[] {
  return (categories ?? []).filter((c) => c.archived_at === null)
}

export function archivedCategories(categories: Category[] | undefined): Category[] {
  return (categories ?? []).filter((c) => c.archived_at !== null)
}

export function lookupById(categories: Category[] | undefined): Map<string, Category> {
  return new Map((categories ?? []).map((c) => [c.id, c]))
}

export function useCategoryLookup(): Map<string, Category> {
  const { data } = useCategories()
  return useMemo(() => lookupById(data), [data])
}

export interface CategoryInput {
  name: string
  color: string | null
}

type CategoryAction =
  | { type: 'create'; input: CategoryInput }
  | { type: 'update'; id: string; input: Partial<CategoryInput> }
  | { type: 'archive' | 'restore' | 'delete'; id: string }

function runCategoryAction(action: CategoryAction): Promise<Category | undefined> {
  switch (action.type) {
    case 'create':
      return api('/api/categories', { method: 'POST', body: action.input })
    case 'update':
      return api(`/api/categories/${action.id}`, { method: 'PATCH', body: action.input })
    case 'delete':
      return api(`/api/categories/${action.id}`, { method: 'DELETE' })
    default:
      return api(`/api/categories/${action.id}/${action.type}`, { method: 'POST' })
  }
}

export function useCategoryAction() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: runCategoryAction,
    onSuccess: (result, action) => {
      // The server's row goes into the cache at once: navigating to a new
      // category must not race the refetch and find it missing.
      queryClient.setQueryData<Category[]>(categoriesKey, (list) =>
        action.type === 'delete' ? removeById(list, action.id) : upsertById(list, result),
      )
      void queryClient.invalidateQueries({ queryKey: categoriesKey })
      // Deleting a category detaches its tasks server-side.
      if (action.type === 'delete') void queryClient.invalidateQueries({ queryKey: tasksKey })
    },
  })
}

/** Named swatches. The column is free text, but offering a fixed set keeps
 *  the sidebar legible against the palette in both colour schemes. */
export const CATEGORY_COLORS = [
  { name: 'Firuze', value: '#2a8c84' },
  { name: 'Saffron', value: '#c08a2e' },
  { name: 'Lapis', value: '#3f6fb0' },
  { name: 'Pomegranate', value: '#b8474a' },
  { name: 'Pistachio', value: '#7b9a3c' },
  { name: 'Plum', value: '#8a5a9e' },
  { name: 'Slate', value: '#66757a' },
] as const

const HEX = /^#[0-9a-f]{6}$/i

/** Only a plain hex reaches a style attribute; anything else falls back. */
export function categoryColor(category: Pick<Category, 'color'> | undefined): string {
  return category?.color && HEX.test(category.color) ? category.color : '#66757a'
}
