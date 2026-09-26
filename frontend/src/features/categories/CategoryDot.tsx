import type { Category } from '@/api/types'
import { cn } from '@/lib/utils'

import { categoryColor } from './api'

export function CategoryDot({ category, className }: { category?: Category; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'inline-block size-2.5 shrink-0 rounded-full',
        category?.archived_at && 'opacity-45',
        className,
      )}
      style={{ backgroundColor: categoryColor(category) }}
    />
  )
}
