/**
 * Centralized color tokens for status and role across the app.
 * All Tailwind classes must be complete strings (no dynamic construction)
 * so the compiler can include them in the output.
 */

// ── Request status ────────────────────────────────────────────────────────────

/** Full badge style: text + border + background */
export const STATUS_BADGE: Record<string, string> = {
  new:            'text-blue-700   border-blue-300   bg-blue-50   dark:text-blue-300   dark:border-blue-800   dark:bg-blue-950/40',
  pending_review: 'text-amber-700  border-amber-300  bg-amber-50  dark:text-amber-300  dark:border-amber-800  dark:bg-amber-950/40',
  escalated:      'text-orange-700 border-orange-300 bg-orange-50 dark:text-orange-300 dark:border-orange-800 dark:bg-orange-950/40',
  reviewed:       'text-indigo-700 border-indigo-300 bg-indigo-50 dark:text-indigo-300 dark:border-indigo-800 dark:bg-indigo-950/40',
  approved:       'text-emerald-700 border-emerald-300 bg-emerald-50 dark:text-emerald-300 dark:border-emerald-800 dark:bg-emerald-950/40',
  rejected:       'text-red-700    border-red-300    bg-red-50    dark:text-red-300    dark:border-red-800    dark:bg-red-950/40',
  withdrawn:      'text-slate-600  border-slate-300  bg-slate-50  dark:text-slate-400  dark:border-slate-700  dark:bg-slate-900/40',
};

/** Small dot color for charts / breakdown pills */
export const STATUS_DOT: Record<string, string> = {
  new:            'bg-blue-400',
  pending_review: 'bg-amber-400',
  escalated:      'bg-orange-400',
  reviewed:       'bg-indigo-400',
  approved:       'bg-emerald-400',
  rejected:       'bg-red-400',
  withdrawn:      'bg-slate-400',
};

/** Inline text color for activity feeds / labels */
export const STATUS_TEXT: Record<string, string> = {
  new:            'text-blue-600   dark:text-blue-400',
  pending_review: 'text-amber-600  dark:text-amber-400',
  escalated:      'text-orange-600 dark:text-orange-400',
  reviewed:       'text-indigo-600 dark:text-indigo-400',
  approved:       'text-emerald-600 dark:text-emerald-400',
  rejected:       'text-red-600    dark:text-red-400',
  withdrawn:      'text-slate-500  dark:text-slate-400',
};

/** Timeline dot (filled circle on audit trail) */
export const STATUS_TIMELINE_DOT: Record<string, string> = {
  new:            'bg-blue-500',
  pending_review: 'bg-amber-500',
  escalated:      'bg-orange-500',
  reviewed:       'bg-indigo-500',
  approved:       'bg-emerald-500',
  rejected:       'bg-red-500',
  withdrawn:      'bg-slate-400',
  submitted:      'bg-blue-500',
  clarified:      'bg-amber-500',
};

// ── KPI tile accent ───────────────────────────────────────────────────────────

export const STATUS_KPI: Record<string, { icon: string; bg: string }> = {
  new:            { icon: 'text-blue-600',    bg: 'bg-blue-50   dark:bg-blue-950/30' },
  pending_review: { icon: 'text-amber-600',   bg: 'bg-amber-50  dark:bg-amber-950/30' },
  escalated:      { icon: 'text-orange-600',  bg: 'bg-orange-50 dark:bg-orange-950/30' },
  reviewed:       { icon: 'text-indigo-600',  bg: 'bg-indigo-50 dark:bg-indigo-950/30' },
  approved:       { icon: 'text-emerald-600', bg: 'bg-emerald-50 dark:bg-emerald-950/30' },
  rejected:       { icon: 'text-red-600',     bg: 'bg-red-50    dark:bg-red-950/30' },
  withdrawn:      { icon: 'text-slate-500',   bg: 'bg-slate-50  dark:bg-slate-900/30' },
  attention:      { icon: 'text-orange-600',  bg: 'bg-orange-50 dark:bg-orange-950/30' },
};

// ── User role ─────────────────────────────────────────────────────────────────

/** Full badge style for role badges */
export const ROLE_BADGE: Record<string, string> = {
  requester:            'text-blue-700   border-blue-300   bg-blue-50   dark:text-blue-300   dark:border-blue-800   dark:bg-blue-950/40',
  approver:             'text-emerald-700 border-emerald-300 bg-emerald-50 dark:text-emerald-300 dark:border-emerald-800 dark:bg-emerald-950/40',
  category_head:        'text-purple-700 border-purple-300 bg-purple-50 dark:text-purple-300 dark:border-purple-800 dark:bg-purple-950/40',
  compliance_reviewer:  'text-amber-700  border-amber-300  bg-amber-50  dark:text-amber-300  dark:border-amber-800  dark:bg-amber-950/40',
};

/** Avatar / initials background + text */
export const ROLE_AVATAR: Record<string, string> = {
  requester:            'bg-blue-100   text-blue-700   dark:bg-blue-950/60   dark:text-blue-300',
  approver:             'bg-emerald-100 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300',
  category_head:        'bg-purple-100 text-purple-700 dark:bg-purple-950/60 dark:text-purple-300',
  compliance_reviewer:  'bg-amber-100  text-amber-700  dark:bg-amber-950/60  dark:text-amber-300',
};

export const ROLE_LABELS: Record<string, string> = {
  requester:           'Requester',
  approver:            'Procurement Manager',
  category_head:       'Category Head',
  compliance_reviewer: 'Compliance Reviewer',
};
