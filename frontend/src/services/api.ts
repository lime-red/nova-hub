import axios from 'axios'

// Create axios instance for Management API
const api = axios.create({
  baseURL: '/management/api/v1',
  withCredentials: true, // Include cookies for session auth
  headers: {
    'Content-Type': 'application/json'
  }
})

// Response interceptor for handling auth errors
api.interceptors.response.use(
  (response) => response,
  (error) => {
    // Redirect to login on 401 (not authenticated)
    if (error.response?.status === 401) {
      // Only redirect if not already on a page that needs no login
      const publicPage = ['/login', '/claim/'].some((p) => window.location.pathname.startsWith(p))
      if (!publicPage) {
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  }
)

export default api

// Auth API functions
export const authApi = {
  login: (username: string, password: string) =>
    api.post('/auth/login', { username, password }),

  logout: () =>
    api.post('/auth/logout'),

  me: () =>
    api.get('/auth/me'),

  changePassword: (currentPassword: string, newPassword: string, confirmPassword: string) =>
    api.post('/auth/change-password', {
      current_password: currentPassword,
      new_password: newPassword,
      confirm_password: confirmPassword
    })
}

// Dashboard API functions
export const dashboardApi = {
  getStats: () =>
    api.get('/dashboard/stats'),

  getActivity: (limit = 20) =>
    api.get('/dashboard/activity', { params: { limit } }),

  getAlerts: (limit = 5) =>
    api.get('/dashboard/alerts', { params: { limit } }),

  getActivityChart: () =>
    api.get('/dashboard/charts/activity'),

  getLeagueChart: () =>
    api.get('/dashboard/charts/leagues'),

  getFull: () =>
    api.get('/dashboard')
}

// Clients API functions
export const clientsApi = {
  list: () =>
    api.get('/clients'),

  get: (id: number) =>
    api.get(`/clients/${id}`),

  create: (data: {
    bbs_name: string
    client_id: string
    city?: string
    state?: string
    country?: string
  }) =>
    api.post('/clients', data),

  update: (id: number, data: {
    bbs_name?: string
    city?: string
    state?: string
    country?: string
    is_active?: boolean
  }) =>
    api.put(`/clients/${id}`, data),

  delete: (id: number) =>
    api.delete(`/clients/${id}`),

  regenerateSecret: (id: number) =>
    api.post(`/clients/${id}/regenerate-secret`),

  addFtnAddress: (id: number, address: string) =>
    api.post(`/clients/${id}/ftn-addresses`, { address }),

  updateFtnAddress: (id: number, addressId: number, address: string) =>
    api.put(`/clients/${id}/ftn-addresses/${addressId}`, { address }),

  deleteFtnAddress: (id: number, addressId: number) =>
    api.delete(`/clients/${id}/ftn-addresses/${addressId}`),

  issueClaimLink: (id: number) =>
    api.post(`/clients/${id}/claim-link`),

  getClaimLink: (id: number) =>
    api.get(`/clients/${id}/claim-link`)
}

// Every BBS against every league: index and address in each
export const addressBookApi = {
  get: () => api.get('/address-book')
}

// Claim links, the sysop's side. Public: the link is the credential.
// GET only looks; POST is the claim, and hands back the secret once.
export const claimApi = {
  status: (token: string) =>
    api.get(`/claim/${token}`),

  claim: (token: string) =>
    api.post(`/claim/${token}`)
}

// Leagues API functions
export const leaguesApi = {
  list: () =>
    api.get('/leagues'),

  get: (id: number) =>
    api.get(`/leagues/${id}`),

  create: (data: {
    league_id: string
    game_type: string
    name: string
    description?: string
    dosemu_path?: string
    game_executable?: string
    hub_fidonet_address?: string
    hub_routes_mail?: boolean
  }) =>
    api.post('/leagues', data),

  update: (id: number, data: {
    name?: string
    description?: string
    dosemu_path?: string
    game_executable?: string
    hub_fidonet_address?: string
    hub_routes_mail?: boolean
    is_active?: boolean
  }) =>
    api.put(`/leagues/${id}`, data),

  delete: (id: number, confirmationName: string) =>
    api.delete(`/leagues/${id}`, { data: { confirmation_name: confirmationName } }),

  addMember: (leagueId: number, data: {
    client_id: number
    bbs_index: number
    ftn_address_id: number
  }) =>
    api.post(`/leagues/${leagueId}/members`, data),

  removeMember: (leagueId: number, memberId: number) =>
    api.delete(`/leagues/${leagueId}/members/${memberId}`),

  // A plain link, not an axios call: the session cookie rides along and the
  // browser saves the file under the name the hub gives it.
  nodelistUrl: (leagueId: number) =>
    `${api.defaults.baseURL}/leagues/${leagueId}/nodelist`,

  updateMember: (leagueId: number, membershipId: number, data: {
    bbs_index?: number
    ftn_address_id?: number
  }) =>
    api.patch(`/leagues/${leagueId}/members/${membershipId}`, data)
}

// Processing API functions
export const processingApi = {
  listRuns: (limit = 50) =>
    api.get('/processing/runs', { params: { limit } }),

  getRun: (id: number) =>
    api.get(`/processing/runs/${id}`),

  trigger: () =>
    api.post('/processing/trigger'),
}

// Movements API functions
//
// What the games said moved between nodes, read from each run's /DETAILED
// transcript. Descriptive rather than diagnostic: the hub reports the traffic
// and the sysop judges it.
export interface Movement {
  id: number
  processing_run_id: number
  league_id: number | null
  league_name: string | null
  occurred_at: string | null
  direction: 'in' | 'out'
  item_type: string
  src_node: number | null
  dst_node: number | null
  size_before: number | null
  size_after: number | null
  phase: string | null
}

export interface MovementSummary {
  since: string
  total: number
  runs: number
  by_type: Array<{ direction: string; item_type: string; count: number }>
  by_pair: Array<{ src_node: number | null; dst_node: number | null; count: number }>
  by_day: Array<{ day: string; count: number }>
  quiet_nodes: number[]
}

export interface MovementFilters {
  days?: number
  league_id?: number
  direction?: 'in' | 'out'
  item_type?: string
  node?: number
  limit?: number
}

export const movementsApi = {
  list: (params: MovementFilters = {}) =>
    api.get<Movement[]>('/movements/', { params }),

  forRun: (runId: number) =>
    api.get<Movement[]>('/movements/', { params: { run_id: runId } }),

  summary: (params: { days?: number; league_id?: number } = {}) =>
    api.get<MovementSummary>('/movements/summary', { params }),
}

// Attacks API functions
//
// Each BRE individual attack, followed through the hub by the ID its result
// echoes. Never carries unit counts: those are hidden game state.
export interface AttackHop {
  is_result: boolean
  packet_id: number
  filename: string
  source_bbs: number | null
  dest_bbs: number | null
  at_hub: string | null
  taken: string | null
}

export type AttackStage =
  | 'result delivered'
  | 'result awaiting pickup'
  | 'awaiting result'
  | 'attack awaiting pickup'
  | 'relay not held'
  | 'not seen'

export interface AttackJourney {
  attack_id: string
  league_id: number
  league_name: string | null
  from_planet: number
  to_planet: number
  attacker: string
  target: string
  attack_type: string | null
  launched: string | null
  resolved: string | null
  attack_at_hub: string | null
  attack_delivered: string | null
  result_at_hub: string | null
  result_delivered: string | null
  stage: AttackStage
  lost_attack_days: number
  mit_due_local: string | null
  mit_due: string | null
  attacker_clock_minutes: number | null
  rollover_after: string | null
  rollover_before: string | null
  unheld_relay_to: number | null
  mit: 'late' | 'possible' | 'overdue' | null
  hops: AttackHop[]
}

export interface AttackFilters {
  days?: number
  league_id?: number
  planet?: number
  attack_id?: string
  mit?: 'late' | 'possible' | 'overdue' | 'any'
  limit?: number
}

// Admin only, fetched per attack on request: hidden game state.
export type AttackUnit = 'troopers' | 'jets' | 'tanks' | 'bombers'

export interface AttackForces {
  attack_id: string
  sent: Record<AttackUnit, number>
  carriers: number
  resolved: boolean
  success: boolean | null
  regions_captured: number | null
  loss_percent: number | null
  lost: Record<AttackUnit, number> | null
  returned: Record<AttackUnit, number> | null
  defenders_destroyed: number | null
}

export const attacksApi = {
  list: (params: AttackFilters = {}) =>
    api.get<AttackJourney[]>('/attacks/', { params }),
  forces: (attackId: string) =>
    api.get<AttackForces>(`/attacks/${attackId}/forces`),
}

// InterBBS traffic other than attacks
export interface TrafficEvent {
  key: string
  kind: string
  role: 'send' | 'result' | 'notice' | null
  from_planet: number
  to_planet: number
  from_letter: string | null
  to_letter: string | null
  stamp: string | null
  at_hub: string | null
  delivered: string | null
  stage: string
  revealable: boolean
  hops: AttackHop[]
}

// A send and its result, a Gooie from funding to its end, or one event.
export interface TrafficJourney {
  key: string
  league_id: number
  league_name: string | null
  kind: string
  from_planet: number
  to_planet: number
  from_letter: string | null
  to_letter: string | null
  recipients: string | null
  expects_result: boolean
  sent_at_hub: string | null
  sent_delivered: string | null
  result_kind: string | null
  result_at_hub: string | null
  result_delivered: string | null
  stage: string
  events: TrafficEvent[]
}

// Admin only, fetched per event on request: hidden game state.
export interface TrafficDetails {
  key: string
  kind: string
  details: Record<string, any>
}

export interface TrafficFilters {
  days?: number
  league_id?: number
  planet?: number
  limit?: number
}

export const trafficApi = {
  list: (params: TrafficFilters = {}) =>
    api.get<TrafficJourney[]>('/traffic/', { params }),
  details: (key: string) =>
    api.get<TrafficDetails>(`/traffic/${key}/details`),
}

// Alerts API functions
export const alertsApi = {
  list: () =>
    api.get('/alerts'),

  get: (id: number) =>
    api.get(`/alerts/${id}`),

  resolve: (id: number, notes?: string) =>
    api.post(`/alerts/${id}/resolve`, { notes }),

  unresolve: (id: number) =>
    api.post(`/alerts/${id}/unresolve`)
}

// Users API functions (admin only)
export const usersApi = {
  list: () =>
    api.get('/users'),

  get: (id: number) =>
    api.get(`/users/${id}`),

  create: (data: { username: string; password: string; is_admin?: boolean }) =>
    api.post('/users', data),

  update: (id: number, data: { username?: string; password?: string; is_admin?: boolean }) =>
    api.put(`/users/${id}`, data),

  delete: (id: number) =>
    api.delete(`/users/${id}`)
}

// System API functions — what build is running
export const systemApi = {
  version: () =>
    api.get('/system/version')
}
