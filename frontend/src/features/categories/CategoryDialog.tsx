import { useId, useState, type FormEvent } from 'react'
import { Check } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

import { describeError } from '@/api/client'
import type { Category } from '@/api/types'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'

import { CATEGORY_COLORS, useCategoryAction } from './api'

interface CategoryDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Present to edit; absent to create. */
  category?: Category
}

export function CategoryDialog({ open, onOpenChange, category }: CategoryDialogProps) {
  // Bumped on each open so the form starts from the category's current
  // values — while staying mounted through the close animation, instead of
  // emptying the dialog as it fades.
  const [opened, setOpened] = useState(0)
  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) setOpened((n) => n + 1)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <CategoryForm key={`${category?.id ?? 'new'}-${opened}`} category={category} onDone={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  )
}

function CategoryForm({ category, onDone }: { category?: Category; onDone: () => void }) {
  const nameId = useId()
  const navigate = useNavigate()
  const action = useCategoryAction()
  const [name, setName] = useState(category?.name ?? '')
  // Editing keeps whatever colour is stored — including none, or one outside
  // the palette — until a swatch is actually picked.
  const [color, setColor] = useState<string | null>(category ? category.color : CATEGORY_COLORS[0].value)

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    const trimmed = name.trim()
    const saved = await action
      .mutateAsync(
        category
          ? {
              type: 'update',
              id: category.id,
              input: {
                ...(trimmed !== category.name && { name: trimmed }),
                ...(color !== category.color && { color }),
              },
            }
          : { type: 'create', input: { name: trimmed, color } },
      )
      .catch(() => undefined)
    if (!saved) return
    // Navigate before closing: closing unmounts this form, and a navigate()
    // from an unmounted component is silently dropped.
    if (!category) navigate(`/c/${saved.id}`)
    onDone()
  }

  return (
    <form onSubmit={onSubmit} className="grid gap-5">
      <DialogHeader>
        <DialogTitle>{category ? 'Edit category' : 'New category'}</DialogTitle>
        <DialogDescription>Group related tasks so you can see where your focus goes.</DialogDescription>
      </DialogHeader>

      <div className="grid gap-2">
        <Label htmlFor={nameId}>Name</Label>
        <Input
          id={nameId}
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={80}
          required
          autoFocus
          className="h-11"
          aria-invalid={action.isError || undefined}
        />
      </div>

      <fieldset className="grid gap-2">
        <legend className="mb-2 text-sm font-medium">Colour</legend>
        <div className="flex flex-wrap gap-2">
          {CATEGORY_COLORS.map((swatch) => {
            const selected = swatch.value === color
            return (
              <label
                key={swatch.value}
                className={cn(
                  'relative grid size-10 cursor-pointer place-items-center rounded-full',
                  'ring-offset-background has-[:focus-visible]:ring-ring has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-offset-2',
                  // Selection is shown by the outline as well as the tick, so
                  // it does not rest on the tick's contrast against the swatch.
                  selected && 'ring-foreground ring-2 ring-offset-2',
                )}
                style={{ backgroundColor: swatch.value }}
                title={swatch.name}
              >
                <input
                  type="radio"
                  name="color"
                  value={swatch.value}
                  checked={selected}
                  onChange={() => setColor(swatch.value)}
                  className="sr-only"
                  aria-label={swatch.name}
                />
                {selected && <Check className="size-5 text-white" aria-hidden="true" />}
              </label>
            )
          })}
        </div>
      </fieldset>

      {action.error && (
        <p role="alert" className="text-destructive text-sm">
          {describeError(action.error)}
        </p>
      )}

      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" disabled={action.isPending || !name.trim()}>
          {category ? 'Save' : 'Create category'}
        </Button>
      </DialogFooter>
    </form>
  )
}

interface ConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  confirmLabel: string
  onConfirm: () => void
}

export function ConfirmDialog({ open, onOpenChange, title, description, confirmLabel, onConfirm }: ConfirmDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md" showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() => {
              onConfirm()
              onOpenChange(false)
            }}
          >
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
