import { cn } from '@/lib/utils'

/** The app's mark: the timer bezel in miniature, a few minutes spent. */
export function Mark({ className }: { className?: string }) {
  const ticks = 12
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true" className={cn('size-6', className)}>
      {Array.from({ length: ticks }, (_, i) => {
        const angle = (i / ticks) * 2 * Math.PI - Math.PI / 2
        const [x1, y1] = [16 + Math.cos(angle) * 10, 16 + Math.sin(angle) * 10]
        const [x2, y2] = [16 + Math.cos(angle) * 14.5, 16 + Math.sin(angle) * 14.5]
        return (
          <line
            key={i}
            x1={x1}
            y1={y1}
            x2={x2}
            y2={y2}
            strokeWidth={2.6}
            strokeLinecap="round"
            className={i < 3 ? 'stroke-current opacity-25' : 'stroke-focus'}
          />
        )
      })}
    </svg>
  )
}
