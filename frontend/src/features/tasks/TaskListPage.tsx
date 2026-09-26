import { useMemo, useRef, useState, type FormEvent } from 'react'
import { Archive, ArchiveRestore, Check, ChevronRight, MoreHorizontal, Pencil, Play, Trash2 } from 'lucide-react'
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import { toast } from 'sonner'

import { describeError } from '@/api/client'
import type { Category, Task } from '@/api/types'
import { Page } from '@/app/Page'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { liveCategories, lookupById, useCategories, useCategoryAction } from '@/features/categories/api'
import { CategoryDialog, ConfirmDialog } from '@/features/categories/CategoryDialog'
import { CategoryDot } from '@/features/categories/CategoryDot'
import { useActiveSession } from '@/features/timer/session'
import { useStartTask } from '@/features/timer/useStartTask'
import { toastError } from '@/lib/toastError'
import { cn } from '@/lib/utils'

import { useTaskAction, useTasks, type TaskAction } from './api'

const NO_CATEGORY = 'none'

function useRunTaskAction() {
  const { mutate } = useTaskAction()
  return (action: TaskAction) =>
    mutate(action, {
      onError: toastError('That didn’t save'),
    })
}

/** Computed once per page and handed to every row, rather than each row
 *  subscribing and building its own lookup. */
interface RowContext {
  categoryLookup: Map<string, Category>
  pickable: Category[]
  runningTaskId: string | undefined
  start: (taskId: string) => void
  starting: boolean
  run: (action: TaskAction) => void
  showCategory: boolean
}

export function TaskListPage() {
  const { categoryId } = useParams()
  const { data: active } = useActiveSession()
  const { start, starting } = useStartTask()
  const run = useRunTaskAction()
  const {
    data: categories,
    isPending: categoriesPending,
    isFetching: categoriesFetching,
    error: categoriesError,
  } = useCategories()
  const { data: tasks, isPending: tasksPending, error } = useTasks()
  // A failed background refetch keeps its data (TanStack v5): show that,
  // with a note, rather than hide a perfectly good list behind the error.
  const loadFailed = error && !tasks
  const category = categoryId ? categories?.find((c) => c.id === categoryId) : undefined
  const categoryLookup = useMemo(() => lookupById(categories), [categories])
  const pickable = useMemo(() => liveCategories(categories), [categories])

  const groups = useMemo(() => {
    const inView = (tasks ?? []).filter((t) => !categoryId || t.category_id === categoryId)
    return {
      open: inView.filter((t) => !t.archived_at && t.status === 'active'),
      done: inView.filter((t) => !t.archived_at && t.status === 'done'),
      archived: inView.filter((t) => t.archived_at),
    }
  }, [tasks, categoryId])

  const row: RowContext = {
    categoryLookup,
    pickable,
    runningTaskId: active?.session?.task_id,
    start,
    starting,
    run,
    showCategory: !categoryId,
  }

  // A stale link to a deleted category — judged only on a settled, successful
  // list, so neither a category created a moment ago nor a failed refetch
  // is mistaken for a missing one.
  if (categoryId && !categoriesPending && !categoriesFetching && !categoriesError && !category) {
    return <Navigate to="/" replace />
  }

  // Until this category is known the page must not fall back to the "All
  // tasks" view: its form would file new tasks under no category at all.
  if (categoryId && !category) {
    return (
      <Page>
        {categoriesError ? (
          <p role="alert" className="text-destructive text-sm">
            Couldn’t load this category: {describeError(categoriesError)}
          </p>
        ) : (
          <p className="text-muted-foreground text-sm">Loading…</p>
        )}
      </Page>
    )
  }

  return (
    <Page>
      <ListHeader category={category} openCount={tasksPending ? undefined : groups.open.length} />

      {(!category || !category.archived_at) && (
        <NewTaskForm category={category} categories={pickable} />
      )}

      {error && tasks && (
        <p role="status" className="text-muted-foreground mt-4 text-sm">
          Couldn’t refresh the list: {describeError(error)}. Showing what was loaded before.
        </p>
      )}
      {loadFailed ? (
        <p role="alert" className="text-destructive mt-10 text-sm">
          Couldn’t load tasks: {describeError(error)}
        </p>
      ) : tasksPending ? (
        <p className="text-muted-foreground mt-10 text-sm">Loading tasks…</p>
      ) : (
        <>
          {groups.open.length === 0 ? (
            <p className="text-muted-foreground mt-10">
              {groups.done.length || groups.archived.length
                ? 'Nothing left open here.'
                : 'Nothing here yet. Add the first thing you want to focus on.'}
            </p>
          ) : (
            <ul className="mt-6 grid">
              {groups.open.map((task) => (
                <TaskRow key={task.id} task={task} row={row} />
              ))}
            </ul>
          )}
          <Collapsible label="Done" tasks={groups.done} row={row} />
          <Collapsible label="Archived" tasks={groups.archived} row={row} />
        </>
      )}
    </Page>
  )
}

function ListHeader({ category, openCount }: { category?: Category; openCount: number | undefined }) {
  const action = useCategoryAction()
  const navigate = useNavigate()
  const [editing, setEditing] = useState(false)
  const [confirmingDelete, setConfirmingDelete] = useState(false)

  const run = (type: 'archive' | 'restore' | 'delete') => {
    if (!category) return
    action.mutate(
      { type, id: category.id },
      {
        onSuccess: () => {
          if (type === 'delete') navigate('/')
        },
        onError: toastError('That didn’t save'),
      },
    )
  }

  return (
    <header className="flex items-start justify-between gap-4">
      <div className="min-w-0">
        <h1 className="flex items-center gap-3 text-2xl font-semibold tracking-tight sm:text-3xl">
          {category && <CategoryDot category={category} className="size-3.5" />}
          <span className={cn('truncate', category?.archived_at && 'text-muted-foreground')}>
            {category ? category.name : 'All tasks'}
          </span>
        </h1>
        <p className="text-muted-foreground mt-1 text-sm">
          {category?.archived_at
            ? 'Archived. Restore it to add tasks here again.'
            : openCount === undefined
              ? '\u00a0' // holds the line so the header does not jump when it loads
              : openCount === 1
                ? '1 open task'
                : `${openCount} open tasks`}
        </p>
      </div>

      {category && (
        <div className="flex shrink-0 items-center gap-2">
          {category.archived_at && (
            <Button variant="outline" className="h-10" onClick={() => run('restore')}>
              <ArchiveRestore aria-hidden="true" />
              Restore
            </Button>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" className="size-10" aria-label={`${category.name} options`}>
                <MoreHorizontal />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-44">
              <DropdownMenuItem onSelect={() => setEditing(true)}>
                <Pencil /> Edit
              </DropdownMenuItem>
              {category.archived_at ? (
                <DropdownMenuItem onSelect={() => run('restore')}>
                  <ArchiveRestore /> Restore
                </DropdownMenuItem>
              ) : (
                <DropdownMenuItem onSelect={() => run('archive')}>
                  <Archive /> Archive
                </DropdownMenuItem>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onSelect={() => setConfirmingDelete(true)}>
                <Trash2 /> Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <CategoryDialog open={editing} onOpenChange={setEditing} category={category} />
          <ConfirmDialog
            open={confirmingDelete}
            onOpenChange={setConfirmingDelete}
            title={`Delete ${category.name}?`}
            description="Its tasks stay, without a category. To keep the grouping for later, archive it instead."
            confirmLabel="Delete category"
            onConfirm={() => run('delete')}
          />
        </div>
      )}
    </header>
  )
}

function NewTaskForm({ category, categories }: { category?: Category; categories: Category[] }) {
  const { mutate, isPending } = useTaskAction()
  const [title, setTitle] = useState('')
  const [pickedCategory, setPickedCategory] = useState(NO_CATEGORY)
  const inputRef = useRef<HTMLInputElement>(null)

  // Only live categories are offered. If the picked one was archived since,
  // fall back rather than submit a choice the picker no longer shows.
  const picked = categories.some((c) => c.id === pickedCategory) ? pickedCategory : NO_CATEGORY

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    const trimmed = title.trim()
    if (!trimmed) return
    mutate(
      {
        type: 'create',
        title: trimmed,
        categoryId: category ? category.id : picked === NO_CATEGORY ? null : picked,
      },
      {
        onSuccess: () => {
          setTitle('')
          inputRef.current?.focus()
        },
        onError: toastError('Couldn’t add the task'),
      },
    )
  }

  return (
    <form onSubmit={onSubmit} className="mt-6 flex flex-col gap-2 sm:flex-row">
      <label htmlFor="new-task" className="sr-only">
        New task
      </label>
      <Input
        id="new-task"
        ref={inputRef}
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder={category ? `Add a task to ${category.name}` : 'Add a task'}
        maxLength={200}
        autoComplete="off"
        className="bg-card h-11 flex-1"
      />
      <div className="flex gap-2">
        {!category && (
          <Select value={picked} onValueChange={setPickedCategory}>
            <SelectTrigger className="bg-card h-11! min-w-0 flex-1 sm:w-40" aria-label="Category">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NO_CATEGORY}>No category</SelectItem>
              {categories.map((c) => (
                <SelectItem key={c.id} value={c.id}>
                  <CategoryDot category={c} />
                  {c.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <Button type="submit" className="h-11 px-5" disabled={isPending || !title.trim()}>
          Add
        </Button>
      </div>
    </form>
  )
}

function Collapsible({ label, tasks, row }: { label: string; tasks: Task[]; row: RowContext }) {
  const [open, setOpen] = useState(false)
  if (tasks.length === 0) return null
  return (
    <section className="mt-8">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="text-muted-foreground hover:text-foreground flex min-h-10 items-center gap-2 text-sm font-medium"
      >
        <ChevronRight className={cn('size-4 transition-transform', open && 'rotate-90')} aria-hidden="true" />
        {label}
        <span className="tabular-nums">{tasks.length}</span>
      </button>
      {open && (
        <ul className="mt-1 grid">
          {tasks.map((task) => (
            <TaskRow key={task.id} task={task} row={row} />
          ))}
        </ul>
      )}
    </section>
  )
}

function TaskRow({ task, row }: { task: Task; row: RowContext }) {
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const { showCategory, run } = row

  const category = task.category_id ? row.categoryLookup.get(task.category_id) : undefined
  const done = task.status === 'done'
  const archived = task.archived_at !== null
  const running = row.runningTaskId === task.id

  return (
    <li
      className={cn(
        'group relative -mx-3 flex min-h-14 items-center gap-2 rounded-lg px-3',
        'hover:bg-accent/60 focus-within:bg-accent/60',
        running && 'bg-focus-tint hover:bg-focus-tint',
      )}
    >
      <button
        type="button"
        role="checkbox"
        aria-checked={done}
        aria-label={done ? `Reopen ${task.title}` : `Mark ${task.title} done`}
        // Not while it is running: the session would finish against a task
        // that is already done.
        disabled={archived || running}
        onClick={() => run({ type: done ? 'reopen' : 'complete', id: task.id })}
        className="focus-visible:ring-ring grid size-11 shrink-0 place-items-center rounded-full focus-visible:ring-2 focus-visible:outline-none disabled:opacity-40"
      >
        <span
          className={cn(
            'grid size-5 place-items-center rounded-full border-2 transition-colors',
            done ? 'border-focus bg-focus text-primary-foreground' : 'border-foreground/30 group-hover:border-foreground/60',
          )}
        >
          {done && <Check className="size-3" strokeWidth={3} aria-hidden="true" />}
        </span>
      </button>

      <div className="min-w-0 flex-1 py-2">
        <Link
          to={`/tasks/${task.id}`}
          className={cn(
            'block truncate underline-offset-4 hover:underline',
            (done || archived) && 'text-muted-foreground',
            done && 'line-through',
          )}
        >
          {task.title}
        </Link>
        {(running || (showCategory && category)) && (
          <p className="text-muted-foreground mt-0.5 flex items-center gap-2 text-xs">
            {running && <span className="text-focus font-medium">In focus now</span>}
            {showCategory && category && (
              <span className="flex min-w-0 items-center gap-1.5">
                <CategoryDot category={category} className="size-2" />
                <span className="truncate">{category.name}</span>
              </span>
            )}
          </p>
        )}
      </div>

      {!done && !archived && !running && (
        <Button
          variant="ghost"
          className="text-focus hover:text-focus h-11 px-3"
          onClick={() => row.start(task.id)}
          disabled={row.starting}
          aria-label={`Start focus on ${task.title}`}
        >
          <Play className="fill-current" aria-hidden="true" />
          <span className="hidden sm:inline">Start</span>
        </Button>
      )}

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" className="size-11 shrink-0" aria-label={`${task.title} options`}>
            <MoreHorizontal />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-48">
          {!archived && (
            <DropdownMenuSub>
              <DropdownMenuSubTrigger>Move to</DropdownMenuSubTrigger>
              <DropdownMenuSubContent className="w-44">
                <DropdownMenuItem
                  disabled={task.category_id === null}
                  onSelect={() => run({ type: 'move', id: task.id, categoryId: null })}
                >
                  No category
                </DropdownMenuItem>
                {row.pickable.map((c) => (
                  <DropdownMenuItem
                    key={c.id}
                    disabled={c.id === task.category_id}
                    onSelect={() => run({ type: 'move', id: task.id, categoryId: c.id })}
                  >
                    <CategoryDot category={c} />
                    {c.name}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuSubContent>
            </DropdownMenuSub>
          )}
          {archived ? (
            <DropdownMenuItem onSelect={() => run({ type: 'restore', id: task.id })}>
              <ArchiveRestore /> Restore
            </DropdownMenuItem>
          ) : (
            // Not while it is running: the session would carry on against a
            // task the lists no longer show.
            <DropdownMenuItem disabled={running} onSelect={() => run({ type: 'archive', id: task.id })}>
              <Archive /> Archive
            </DropdownMenuItem>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            variant="destructive"
            disabled={running}
            onSelect={() => setConfirmingDelete(true)}
          >
            <Trash2 /> Delete
          </DropdownMenuItem>
          {running && (
            <p className="text-muted-foreground px-2 py-1.5 text-xs">
              Finish or discard the session to archive or delete this task.
            </p>
          )}
        </DropdownMenuContent>
      </DropdownMenu>

      <ConfirmDialog
        open={confirmingDelete}
        onOpenChange={setConfirmingDelete}
        title="Delete this task?"
        description={`“${task.title}” will be removed from your lists.`}
        confirmLabel="Delete task"
        // Re-checked on confirm: the dialog may have opened before a session
        // started on this task elsewhere.
        onConfirm={() => {
          if (row.runningTaskId === task.id) {
            toast('This task is in focus now', {
              description: 'Finish or discard the session first, then delete it.',
            })
            return
          }
          run({ type: 'delete', id: task.id })
        }}
      />
    </li>
  )
}
