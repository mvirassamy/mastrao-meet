import { ApiError } from '@/api/ApiError'
import { fetchApi } from '@/api/fetchApi'

type UsageBudgetDescriptor = {
  organization_slug: string
  usage_budget_url: string
}

export const fetchUsageBudget = async () => {
  try {
    return await fetchApi<UsageBudgetDescriptor>('usage-budget/')
  } catch (error) {
    if (error instanceof ApiError && error.statusCode === 404) return null
    throw error
  }
}
