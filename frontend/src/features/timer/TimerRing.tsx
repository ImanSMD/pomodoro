import { Fragment, type ReactNode } from 'react'

import type { SessionKind } from '@/api/types'
import { cn } from '@/lib/utils'

import { formatClock, KIND_LABEL } from './countdown'

// One segment per planned minute, up to this many; beyond it the segments
// stand for equal fractions instead, or a long session would be a grey blur.
const MAX_SEGMENTS = 90
const R = 90
const STROKE = 11

function point(deg: number): string {
  const rad = ((deg - 90) * Math.PI) / 180
  return `${(100 + R * Math.cos(rad)).toFixed(3)} ${(100 + R * Math.sin(rad)).toFixed(3)}`
}

function arc(fromDeg: number, toDeg: number): string {
  const large = toDeg - fromDeg > 180 ? 1 : 0
  return `M ${point(fromDeg)} A ${R} ${R} 0 ${large} 1 ${point(toDeg)}`
}

export function segmentCount(plannedMinutes: number): number {
  return plannedMinutes >= 1 && plannedMinutes <= MAX_SEGMENTS ? Math.round(plannedMinutes) : 60
}

interface TimerRingProps {
  plannedMinutes: number
  remainingMs: number
  kind: SessionKind
  /** Nothing running: the ring shows what a start would give, quietly. */
  idle?: boolean
  children?: ReactNode
}

export function TimerRing({ plannedMinutes, remainingMs, kind, idle = false, children }: TimerRingProps) {
  const n = segmentCount(plannedMinutes)
  const totalMs = plannedMinutes * 60_000
  const spent = Math.min(n, Math.max(0, (1 - remainingMs / totalMs) * n))

  const step = 360 / n
  const gap = Math.min(2.6, step * 0.32)
  const clock = formatClock(remainingMs)
  const tone = kind === 'work' ? 'stroke-focus' : 'stroke-rest'

  return (
    <div
      className="@container relative aspect-square w-full"
      role={idle ? undefined : 'timer'}
      aria-label={idle ? undefined : `${clock} left, ${KIND_LABEL[kind].toLowerCase()}`}
    >
      <svg viewBox="0 0 200 200" className="size-full" aria-hidden="true">
        {Array.from({ length: n }, (_, i) => {
          const from = i * step + gap / 2
          const to = (i + 1) * step - gap / 2
          // Time drains from twelve o'clock clockwise: the spent minutes are
          // the ones already passed, and the current one shrinks by the second.
          const used = Math.min(1, Math.max(0, spent - i))
          const cut = from + (to - from) * used
          return (
            <g key={i} fill="none" strokeWidth={STROKE} strokeLinecap="butt">
              <path d={arc(from, to)} className="stroke-foreground/10" />
              {used < 1 && (
                <path
                  d={arc(cut, to)}
                  className={cn(tone, idle && 'opacity-35')}
                />
              )}
            </g>
          )
        })}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span
          className={cn(
            'leading-none font-medium tracking-tight',
            clock.length > 5 ? 'text-[15cqw]' : 'text-[21cqw]',
            idle && 'text-muted-foreground',
          )}
        >
          {clock.split(':').map((part, i) => (
            <Fragment key={i}>
              {/* Tabular figures keep the digits from jittering; the colon is
                  set proportionally, or tnum pads it to a full digit width. */}
              {i > 0 && <span className="relative -top-[0.07em] [font-variant-numeric:normal]">:</span>}
              <span className="tabular-nums">{part}</span>
            </Fragment>
          ))}
        </span>
        <span
          className={cn(
            'mt-[3cqw] text-[4.4cqw] font-medium',
            kind === 'work' ? 'text-focus' : 'text-rest-ink',
          )}
        >
          {KIND_LABEL[kind]}
        </span>
        {children}
      </div>
    </div>
  )
}
