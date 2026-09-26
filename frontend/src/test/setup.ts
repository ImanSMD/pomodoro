import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// Testing Library only auto-unmounts when test globals are on. Without this,
// hooks from earlier tests stay mounted and react to later tests' events —
// a stray visibilitychange once made one test's hook claim another's session.
afterEach(() => cleanup())

// Restored here rather than at the end of each test, where a failing
// assertion would skip it and leave every later test on fake timers.
afterEach(() => vi.useRealTimers())

// vi.stubGlobal outlives the test that made it — a Web Locks mock once leaked
// into every later test in its file. Each test stubs what it needs afresh.
afterEach(() => vi.unstubAllGlobals())
