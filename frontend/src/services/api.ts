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
      // Only redirect if not already on login page
      if (!window.location.pathname.includes('/login')) {
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

  create: (data: { bbs_name: string; client_id: string }) =>
    api.post('/clients', data),

  update: (id: number, data: { bbs_name?: string; is_active?: boolean }) =>
    api.put(`/clients/${id}`, data),

  delete: (id: number) =>
    api.delete(`/clients/${id}`),

  regenerateSecret: (id: number) =>
    api.post(`/clients/${id}/regenerate-secret`)
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
  }) =>
    api.post('/leagues', data),

  update: (id: number, data: {
    name?: string
    description?: string
    dosemu_path?: string
    game_executable?: string
    is_active?: boolean
  }) =>
    api.put(`/leagues/${id}`, data),

  delete: (id: number) =>
    api.delete(`/leagues/${id}`),

  addMember: (leagueId: number, data: {
    client_id: number
    bbs_index: number
    fidonet_address: string
  }) =>
    api.post(`/leagues/${leagueId}/members`, data),

  removeMember: (leagueId: number, memberId: number) =>
    api.delete(`/leagues/${leagueId}/members/${memberId}`),

  updateBbsIndex: (leagueId: number, membershipId: number, bbsIndex: number) =>
    api.put(`/leagues/${leagueId}/members/${membershipId}/bbs-index`, { bbs_index: bbsIndex }),

  updateFidonet: (leagueId: number, membershipId: number, fidonetAddress: string) =>
    api.put(`/leagues/${leagueId}/members/${membershipId}/fidonet`, { fidonet_address: fidonetAddress })
}

// Processing API functions
export const processingApi = {
  listRuns: (limit = 50) =>
    api.get('/processing/runs', { params: { limit } }),

  getRun: (id: number) =>
    api.get(`/processing/runs/${id}`),

  trigger: () =>
    api.post('/processing/trigger')
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
