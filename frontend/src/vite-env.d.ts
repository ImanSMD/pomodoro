/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Set by compose from API_PORT. Unset means the API is on the same origin. */
  readonly VITE_API_URL?: string
}
