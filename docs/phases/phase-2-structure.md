# Phase 2 — Todos, Tags, Per-Task Settings, History

**Outcome:** a task stops being a title and becomes a workspace — a checklist inside it, tags across
it, its own timer settings, and a history log of everything that ever happened to it.

Depends on Phase 1's `scoped()` helper and `CurrentUser` dependency; all new endpoints reuse both.

> **Workflow:** **Do not start this phase until Phase 1 has passed its exit gate.** Work through the sections in order, running
> **code → tests → code review → fix** on each one until a review pass comes back clean.
> See [`docs/WORKFLOW.md`](../WORKFLOW.md) for the full rules.

---

## 2.1 Todos (checklist inside a task)

**Migration 0004** — `todos (id, user_id, task_id → tasks, text, done, done_at, position, …)`.

```
GET    /api/tasks/{task_id}/todos
POST   /api/tasks/{task_id}/todos     {text}
PATCH  /api/todos/{id}                {text?, done?}     → sets/clears done_at
DELETE /api/todos/{id}                                   → soft delete
PATCH  /api/tasks/{task_id}/todos/reorder   {ids: [...]}
```

Carry `user_id` on `todos` directly rather than only reaching it through `task_id`. It denormalises
one column but lets `scoped()` work unchanged and keeps every ownership check a single predicate
instead of a join.

Reorder rewrites `position` as a dense `0..n-1` sequence from the submitted id list, in one
`UPDATE ... FROM (VALUES ...)`. Validate that the submitted set matches the task's current todo set
exactly, or a stale client can silently drop rows.

---

## 2.2 Tags

**Migration 0005** — `tags (id, user_id, name, color)` + `task_tags (task_id, tag_id)` composite PK,
and `UNIQUE (user_id, lower(name)) WHERE deleted_at IS NULL`.

```
GET/POST/PATCH/DELETE  /api/tags
PUT    /api/tasks/{id}/tags    {tag_ids: [...]}   → replaces the whole set
GET    /api/tasks?tag_ids=a,b                     → AND semantics (has all)
```

`PUT`-the-whole-set is deliberate: it makes the request idempotent and sidesteps the
add-one/remove-one race where two open tabs each think they know the final tag list.

---

## 2.3 Per-task timer settings

**Migration 0006** — add nullable `work_minutes`, `break_minutes`, `long_break_minutes`,
`rounds_before_long_break` to `tasks`. **Nullable means inherit** — never copy the user default into
the task at creation, or changing your global default later mysteriously fails to affect tasks you
already made.

```
GET   /api/tasks/{id}/effective-settings
      → {work_minutes: 50, source: {work_minutes: "task", break_minutes: "user"}}
```

The `source` map lets the settings UI show "inherited" vs "overridden" honestly, and gives the
frontend one resolution point instead of duplicating the fallback logic. Session start already
resolves through the same helper — extract it to `app/core/settings.py` and call it from both.

---

## 2.4 History / audit log

**Migration 0007** — `events (id, user_id, entity_type, entity_id, action, payload jsonb, created_at)`
with an index on `(user_id, entity_type, entity_id, created_at DESC)`.

`app/core/audit.py`:

```python
async def write_event(db, user, entity, action, payload=None): ...
```

Called on create · update · archive · restore · delete · complete for categories, tasks, todos, and
sessions. For updates store a **diff**, not the whole row — `{"title": {"from": "a", "to": "b"}}` —
so the log stays readable and doesn't balloon.

```
GET /api/tasks/{id}/history?limit=50
GET /api/activity?limit=50            → the user's whole recent stream
```

Write events in the **same transaction** as the mutation. A separate commit means a crash between
the two leaves a log that disagrees with reality, which is worse than no log.

Never put `password_hash` or tokens in `payload` — add an explicit field denylist in
`write_event` rather than trusting callers.

---

## 2.5 Frontend

- **Task detail page** — the real work of this phase. Header (title, category, tags, archive), an
  inline-editable description, the todo checklist, a settings panel, and a history timeline.
- **Todo checklist** — optimistic toggle via TanStack Query `onMutate` with rollback on error,
  drag-to-reorder, "add todo" input that stays focused for rapid entry.
- **Tag picker** — combobox with create-on-type; tag chips on task cards in the list.
- **Settings panel** — each field shows its inherited value as placeholder text with an
  "override" affordance; clearing a field returns it to inherited.
- **History timeline** — grouped by day, using the Phase 1 date helper.

---

## 2.6 Tests

| file | covers |
|---|---|
| `test_todos.py` | CRUD · reorder is dense and rejects a mismatched id set · soft delete hides |
| `test_tags.py` | duplicate name per user → 409 · `PUT` replaces the set · multi-tag filter is AND |
| `test_settings_resolution.py` | task override wins · null inherits · `source` map correct · a new session freezes the resolved value |
| `test_audit.py` | each mutation writes exactly one event · update stores a diff · a failed mutation writes **no** event |

---

## Definition of done

- [ ] Add todos to a task, reorder them, tick one — survives refresh
- [ ] Create a tag, apply it to two tasks, filter the list by it
- [ ] Override a task's work length; a new session on it uses the override, others still inherit
- [ ] Change a global default; tasks with no override follow it, overridden ones don't
- [ ] Archive then restore a task and see both actions in its history
- [ ] `pytest` green

---

## Todo

**Todos**
- [ ] Migration 0004 — `todos` (with denormalised `user_id`)
- [ ] CRUD endpoints + `done_at` handling
- [ ] Reorder endpoint with id-set validation
- [ ] Checklist UI with optimistic toggle + drag reorder

**Tags**
- [ ] Migration 0005 — `tags` + `task_tags` + per-user unique name
- [ ] Tag CRUD, `PUT /tasks/{id}/tags`, AND-filtering on task list
- [ ] Tag combobox with create-on-type; chips on task cards

**Per-task settings**
- [ ] Migration 0006 — nullable override columns on `tasks`
- [ ] Extract `core/settings.py` resolver; call from session start *and* the new endpoint
- [ ] `GET /tasks/{id}/effective-settings` with `source` map
- [ ] Settings panel showing inherited vs overridden

**History**
- [ ] Migration 0007 — `events` + composite index
- [ ] `core/audit.py` `write_event` with field denylist
- [ ] Wire into every mutation, same transaction
- [ ] Diff payloads on update
- [ ] `/tasks/{id}/history` + `/activity`
- [ ] Timeline UI grouped by day

**Frontend & tests**
- [ ] Task detail page assembling all four features
- [ ] `test_todos.py`, `test_tags.py`, `test_settings_resolution.py`, `test_audit.py`

---

## Exit gate

Phase 2 is not finished until every item in the gate checklist in
[`docs/WORKFLOW.md`](../WORKFLOW.md#exit-gate) passes:
todos ticked · `pytest` fully green · `/code-review high` clean over the phase diff ·
Definition of done verified by hand in a browser · a clean-volume rebuild
(`docker compose down -v && docker compose up --build`) walked end to end · committed and merged.

Run `/security-review` if any ownership check changes — this phase adds three new scoped entities.

**Phase 3 does not begin until this gate passes.**
