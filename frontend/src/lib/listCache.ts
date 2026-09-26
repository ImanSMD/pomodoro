/** Put the server's row into a cached list: replace it by id, or append. */
export function upsertById<T extends { id: string }>(list: T[] | undefined, row: T | undefined): T[] | undefined {
  if (!list || !row) return list
  return list.some((item) => item.id === row.id)
    ? list.map((item) => (item.id === row.id ? row : item))
    : [...list, row]
}

export function removeById<T extends { id: string }>(list: T[] | undefined, id: string): T[] | undefined {
  return list?.filter((item) => item.id !== id)
}
