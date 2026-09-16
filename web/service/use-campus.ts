import { useQuery } from '@tanstack/react-query'
import { get } from './base'

export type CampusKnowledgeLimits = {
  max_datasets_per_workspace: number
  max_documents_per_dataset: number
}

/**
 * Read the platform knowledge limits the Campus backend enforces for this
 * student workspace. The console API prefix already points at the Campus host,
 * and the campus portal cookie is same-origin, so the plain `get` is enough.
 */
export const useCampusKnowledgeLimits = () => {
  return useQuery<CampusKnowledgeLimits>({
    queryKey: ['campus', 'knowledge-limits'],
    queryFn: () => get<CampusKnowledgeLimits>('/campus/knowledge-limits'),
    staleTime: 5 * 60 * 1000,
    retry: false,
  })
}
