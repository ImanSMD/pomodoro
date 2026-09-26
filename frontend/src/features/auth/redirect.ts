/**
 * A post-login destination taken from history state, reduced to an in-app
 * path. Browsers read "//host" — and "/\\host", since they treat a backslash
 * as a slash — as another origin, so either would make this an open redirect.
 */
export function safeRedirect(from: string | undefined): string {
  if (!from || !from.startsWith('/') || from.startsWith('//') || from.includes('\\')) return '/'
  return from
}
