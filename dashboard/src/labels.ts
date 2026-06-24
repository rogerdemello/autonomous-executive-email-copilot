/*
 * Human-language mapping — the single source of truth that keeps backend/RL
 * jargon off the screen. Components import from here so a non-technical reader
 * (an exec / ops / HR reviewer) never sees raw enums like `task_id`,
 * `chill_manager`, or `escalate`.
 */

export type Tone = 'neutral' | 'accent' | 'warn' | 'danger' | 'ok'

interface LabelInfo {
  label: string
  tone?: Tone
  hint?: string
}

/* ---- Workload (backend: task_id) ------------------------------------- */

export const WORKLOADS: { id: string; label: string; hint: string }[] = [
  {
    id: 'easy_classification',
    label: 'Triage practice',
    hint: 'Sort a small inbox by importance',
  },
  {
    id: 'medium_prioritization',
    label: 'Prioritize my morning',
    hint: 'Rank a busier inbox and act on what matters',
  },
  {
    id: 'hard_full_management',
    label: 'Run my inbox',
    hint: 'Full management under time pressure',
  },
]

export function workloadLabel(taskId: string): string {
  return WORKLOADS.find((w) => w.id === taskId)?.label ?? taskId
}

/* ---- Management style (backend: persona) ----------------------------- */

export const MANAGEMENT_STYLES: { id: string; label: string; hint: string }[] = [
  { id: 'strict_ceo', label: 'Strict (CEO)', hint: 'Low tolerance for missed or risky items' },
  { id: 'balanced', label: 'Balanced', hint: 'Sensible defaults for most teams' },
  { id: 'chill_manager', label: 'Relaxed', hint: 'Forgiving pace, fewer interruptions' },
]

export function styleLabel(persona: string): string {
  return MANAGEMENT_STYLES.find((p) => p.id === persona)?.label ?? persona
}

/* ---- Actions (backend: action_type) ---------------------------------- */

const ACTIONS: Record<string, LabelInfo> = {
  classify: { label: 'Sorted', tone: 'neutral' },
  prioritize: { label: 'Prioritized', tone: 'accent' },
  reply: { label: 'Replied', tone: 'ok' },
  defer: { label: 'Deferred', tone: 'warn' },
  escalate: { label: 'Escalated', tone: 'danger' },
}

export function actionInfo(actionType: string): LabelInfo {
  return ACTIONS[actionType] ?? { label: actionType, tone: 'neutral' }
}

/* ---- Risk (backend: risk_tag) ---------------------------------------- */

const RISKS: Record<string, LabelInfo> = {
  none: { label: 'No risk', tone: 'neutral' },
  legal: { label: 'Legal', tone: 'danger' },
  security: { label: 'Security', tone: 'danger' },
  finance: { label: 'Finance', tone: 'warn' },
  ops: { label: 'Operations', tone: 'warn' },
}

export function riskInfo(riskTag: string): LabelInfo {
  return RISKS[riskTag] ?? { label: riskTag, tone: 'neutral' }
}

/* ---- Priority (backend: priority_hint) ------------------------------- */

const PRIORITIES: Record<string, LabelInfo> = {
  urgent: { label: 'Urgent', tone: 'danger' },
  high: { label: 'High', tone: 'warn' },
  normal: { label: 'Normal', tone: 'neutral' },
  low: { label: 'Low', tone: 'neutral' },
}

export function priorityInfo(hint: string): LabelInfo {
  return PRIORITIES[hint] ?? { label: hint, tone: 'neutral' }
}

/* ---- Overall risk level (backend: risk_level) ------------------------ */

export function riskLevelTone(level: string): Tone {
  if (level === 'high') return 'danger'
  if (level === 'medium') return 'warn'
  return 'ok'
}

/* ---- Sender role (backend: sender_role) ------------------------------ */

export function senderRoleLabel(role: string): string {
  const map: Record<string, string> = {
    client: 'Client',
    internal: 'Internal',
    vendor: 'Vendor',
    unknown: 'Unknown sender',
  }
  return map[role] ?? role
}

/* ---- Copilot decision status (backend: AIStatusType) ----------------- */

export function decisionStatus(status: string): LabelInfo {
  if (status === 'success') return { label: 'Handled by copilot', tone: 'ok' }
  if (status.startsWith('fallback')) return { label: 'Used a safe default', tone: 'warn' }
  return { label: 'Needed attention', tone: 'danger' }
}

/* ---- Importance (backend: business_value 0..1) ----------------------- */

export function importanceInfo(value: number): LabelInfo {
  if (value >= 0.7) return { label: 'High importance', tone: 'danger' }
  if (value >= 0.4) return { label: 'Medium importance', tone: 'warn' }
  return { label: 'Low importance', tone: 'neutral' }
}

/* ---- Deadlines (backend: deadline_minutes) --------------------------- */

export function deadlineLabel(minutes: number): string {
  if (minutes <= 0) return 'Overdue'
  if (minutes < 60) return `Due in ${minutes} min`
  const hours = Math.round(minutes / 60)
  return hours === 1 ? 'Due in ~1 hour' : `Due in ~${hours} hours`
}
