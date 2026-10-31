<script setup lang="ts">
// One row per BBS per league: when its client last polled the league, what is
// waiting for it, and what the hub's copy of the game says about it. Shared by
// the client page (a row per league) and the league page (a row per member).
import { ref } from 'vue'
import { leaguesApi, type LeagueStatusRow } from '@/services/api'

defineProps<{
  rows: LeagueStatusRow[]
  trackingSince: string | null
  by: 'league' | 'client'
  loading?: boolean
}>()

const expanded = ref<Set<string>>(new Set())
const key = (r: LeagueStatusRow) => `${r.league_id}:${r.client_id}`

function toggle(r: LeagueStatusRow) {
  const k = key(r)
  const next = new Set(expanded.value)
  if (next.has(k)) next.delete(k)
  else next.add(k)
  expanded.value = next
}

function at(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : ''
}

function ago(iso: string | null): string {
  if (!iso) return ''
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (secs < 90) return 'just now'
  if (secs < 3600) return `${Math.round(secs / 60)} min ago`
  if (secs < 2 * 86400) return `${Math.round(secs / 3600)} h ago`
  return `${Math.round(secs / 86400)} days ago`
}

function index(r: LeagueStatusRow): string {
  return r.bbs_index == null ? '-' : r.bbs_index.toString(16).toUpperCase().padStart(2, '0')
}

const STATE_BADGE: Record<string, string> = {
  ok: 'badge-success',
  out_of_date: 'badge-info',
  game_silent: 'badge-warning',
  backlog: 'badge-warning',
  not_member: 'badge-info',
}

function badge(r: LeagueStatusRow): string {
  return STATE_BADGE[r.state] ?? 'badge-danger'
}

function gameText(r: LeagueStatusRow): string {
  const g = r.game
  if (!g) return 'No listing yet'
  if (!g.heard) return g.source === 'bbsinfo' ? 'No recons received' : 'No TimeCheck answered'
  return `Last ${g.source === 'bbsinfo' ? 'recon' : 'TimeCheck'} ${ago(g.last_heard)}`
}

function gameTitle(r: LeagueStatusRow): string {
  const g = r.game
  if (!g) return 'The hub has no BBSINFO for this league yet'
  const lines = []
  if (g.line) lines.push(g.line.trim())
  if (g.last_heard) lines.push(`Last heard ${at(g.last_heard)}`)
  if (g.as_of) lines.push(`As of ${at(g.as_of)}`)
  if (g.routing_missing) lines.push('ROUTEINFO: information missing')
  return lines.join('\n')
}

</script>

<template>
  <table class="table league-status">
    <thead>
      <tr>
        <th v-if="by === 'client'">League</th>
        <th v-else>BBS</th>
        <th>Index</th>
        <th>Polled</th>
        <th>Waiting</th>
        <th>Downloaded</th>
        <th>Uploaded</th>
        <th>Hub game's view</th>
        <th>State</th>
      </tr>
    </thead>
    <tbody>
      <tr v-if="loading && !rows.length">
        <td colspan="8" class="text-center text-muted" style="padding: 2rem;">Loading…</td>
      </tr>
      <tr v-else-if="!rows.length">
        <td colspan="8" class="text-center text-muted" style="padding: 2rem;">
          {{ by === 'client' ? 'Not a member of any leagues' : 'No members' }}
        </td>
      </tr>
      <template v-for="r in rows" :key="key(r)">
        <tr>
          <td v-if="by === 'client'">
            <router-link :to="`/leagues/${r.league_id}`">{{ r.league_name }} ({{ r.full_id }})</router-link>
            <div v-if="r.member" class="sub">
              <a class="font-mono" :href="leaguesApi.nodelistUrl(r.league_id)" download>nodelist</a>
            </div>
          </td>
          <td v-else>
            <router-link v-if="!r.restricted" :to="`/clients/${r.client_id}`">{{ r.bbs_name }}</router-link>
            <span v-else>{{ r.bbs_name }}</span>
          </td>
          <td class="font-mono" :title="r.fidonet_address ? `FTN address ${r.fidonet_address}` : ''">
            {{ index(r) }}
          </td>
          <td class="nowrap">
            <span v-if="r.restricted" class="text-muted">-</span>
            <span v-else-if="r.last_poll_at" :title="[at(r.last_poll_at), r.user_agent].filter(Boolean).join('\n')">
              {{ ago(r.last_poll_at) }}
            </span>
            <span v-else class="text-danger" :title="trackingSince ? `Polls recorded since ${at(trackingSince)}` : ''">
              Never
            </span>
          </td>
          <td class="nowrap">
            <span v-if="r.waiting == null" class="text-muted">-</span>
            <a v-else-if="r.waiting > 0" href="#" @click.prevent="toggle(r)"
               :class="{ 'text-warning': r.state === 'backlog' || r.state === 'not_polling' }">
              {{ r.waiting }}, oldest {{ ago(r.oldest_waiting_at) }}
            </a>
            <span v-else>0</span>
          </td>
          <td class="nowrap" :title="at(r.last_downloaded_at)">{{ r.restricted ? '-' : ago(r.last_downloaded_at) || 'Never' }}</td>
          <td class="nowrap" :title="at(r.last_uploaded_at)">{{ r.restricted ? '-' : ago(r.last_uploaded_at) || 'Never' }}</td>
          <td :title="gameTitle(r)">
            {{ gameText(r) }}
            <div v-if="r.game?.version" class="sub font-mono">{{ r.game.version }}</div>
          </td>
          <td>
            <span class="badge" :class="badge(r)">{{ r.state_label }}</span>
            <div v-if="r.state_detail" class="sub">{{ r.state_detail }}</div>
          </td>
        </tr>
        <tr v-if="expanded.has(key(r))" class="waiting">
          <td colspan="8">
            <span v-for="p in r.waiting_packets" :key="p.filename" class="font-mono waiting-packet"
                  :title="`Ready since ${at(p.processed_at)}`">{{ p.filename }}</span>
            <span v-if="(r.waiting ?? 0) > r.waiting_packets.length" class="text-muted">
              and {{ (r.waiting ?? 0) - r.waiting_packets.length }} more
            </span>
          </td>
        </tr>
      </template>
    </tbody>
  </table>
</template>

<style scoped>
.sub {
  font-size: 0.8rem;
  color: var(--color-text-muted);
}
.nowrap {
  white-space: nowrap;
}
.waiting td {
  background: var(--color-bg);
}
.waiting-packet {
  display: inline-block;
  margin-right: 1rem;
}
</style>
