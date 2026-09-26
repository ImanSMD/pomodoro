import { toast } from 'sonner'

import { describeError } from '@/api/client'

/** The one way a failed action is reported: a title saying what did not
 *  happen, and the reason in words a person can use. */
export function toastError(title: string): (error: unknown) => void {
  return (error) => toast.error(title, { description: describeError(error) })
}
