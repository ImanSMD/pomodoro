import type { ReactNode } from 'react'

/** The reading column every page sits in. */
export function Page({ children }: { children: ReactNode }) {
  return <div className="mx-auto w-full max-w-2xl px-4 pt-6 pb-24 sm:px-8 lg:pt-12">{children}</div>
}
