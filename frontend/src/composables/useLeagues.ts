/**
 * The leagues the signed-in user can see, for filters and labels. Fetched once
 * and shared by every page and filter bar that asks, so moving between pages
 * doesn't refetch it.
 */
import { computed, ref } from 'vue'
import { leaguesApi } from '@/services/api'

export interface LeagueOption {
  id: number
  name: string
  full_id: string
}

const leagues = ref<LeagueOption[]>([])
let loading: Promise<void> | null = null

function load(): Promise<void> {
  if (!loading) {
    loading = leaguesApi.list()
      .then(({ data }) => { leagues.value = data })
      // The league filter is a convenience; the pages work without it. Try
      // again next time something asks.
      .catch(() => { loading = null })
  }
  return loading
}

// "015B Medusa Test League 015": two leagues can share a number and even a
// name, one BRE and one FE, so the name alone doesn't say which.
const labels = computed(() => new Map(leagues.value.map((l) => [l.id, `${l.full_id} ${l.name}`])))

export function useLeagues() {
  load()
  function leagueLabel(id: number | null | undefined, name?: string | null): string {
    return (id != null && labels.value.get(id)) || name || '-'
  }
  return { leagues, leagueLabel, reload: () => { loading = null; return load() } }
}
