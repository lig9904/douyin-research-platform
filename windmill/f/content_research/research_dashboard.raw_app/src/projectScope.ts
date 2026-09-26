import React, { createContext, useContext } from 'react'

export type ProjectScope =
  | { mode: 'project'; projectId: string; projectName: string }
  | { mode: 'legacy-admin' }

export type ProjectRosterItem = {
  id: string
  name: string
  organization_name?: string | null
  member_role?: string | null
  can_review_l3?: boolean
}

export type ProjectScopeContextValue = {
  scope: ProjectScope | null
  projects: ProjectRosterItem[]
  legacyAdmin: boolean
  loading: boolean
  error: string
  chooseScope: (scope: ProjectScope) => void
}

const ProjectScopeContext = createContext<ProjectScopeContextValue | null>(null)

export const ProjectScopeProvider = ProjectScopeContext.Provider

export function useProjectScope() {
  const value = useContext(ProjectScopeContext)
  if (!value) throw new Error('ProjectScopeProvider is required')
  return value
}
