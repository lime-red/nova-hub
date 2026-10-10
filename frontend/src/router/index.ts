import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { requiresAuth: false }
    },
    {
      // A sysop collecting their credentials: no login, the link is the credential.
      path: '/claim/:token',
      name: 'claim',
      component: () => import('@/views/ClaimView.vue'),
      meta: { requiresAuth: false }
    },
    {
      // Connecting a sign-in to an existing account: the link names the account.
      path: '/relink/:token',
      name: 'relink',
      component: () => import('@/views/RelinkView.vue'),
      meta: { requiresAuth: false }
    },
    {
      path: '/',
      redirect: '/dashboard'
    },
    {
      path: '/dashboard',
      name: 'dashboard',
      component: () => import('@/views/DashboardView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/clients',
      name: 'clients',
      component: () => import('@/views/ClientsView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/clients/:id',
      name: 'client-detail',
      component: () => import('@/views/ClientDetailView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/address-book',
      name: 'address-book',
      component: () => import('@/views/AddressBookView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/leagues',
      name: 'leagues',
      component: () => import('@/views/LeaguesView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/leagues/:id',
      name: 'league-detail',
      component: () => import('@/views/LeagueDetailView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/processing',
      name: 'processing',
      component: () => import('@/views/ProcessingView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/processing/:id',
      name: 'processing-detail',
      component: () => import('@/views/ProcessingDetailView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/movements',
      name: 'movements',
      component: () => import('@/views/MovementsView.vue'),
      meta: { requiresAuth: true, requiresAdmin: true }
    },
    {
      path: '/attacks',
      name: 'attacks',
      component: () => import('@/views/AttacksView.vue'),
      meta: { requiresAuth: true, requiresAdmin: true }
    },
    {
      path: '/traffic',
      redirect: '/attacks'
    },
    {
      path: '/alerts',
      name: 'alerts',
      component: () => import('@/views/AlertsView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/admin/users',
      name: 'admin-users',
      component: () => import('@/views/AdminUsersView.vue'),
      meta: { requiresAuth: true, requiresAdmin: true }
    },
    {
      path: '/admin/audit',
      name: 'admin-audit',
      component: () => import('@/views/AuditView.vue'),
      meta: { requiresAuth: true, requiresAdmin: true }
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      redirect: '/dashboard'
    }
  ]
})

// Navigation guard for authentication
router.beforeEach(async (to, _from, next) => {
  const authStore = useAuthStore()

  // Try to check auth status if not already done
  if (!authStore.initialized) {
    await authStore.checkAuth()
  }

  // Redirect to login if auth required but not authenticated
  if (to.meta.requiresAuth && !authStore.isAuthenticated) {
    next({ name: 'login', query: { redirect: to.fullPath } })
    return
  }

  // Redirect to dashboard if already authenticated and trying to access login
  if (to.name === 'login' && authStore.isAuthenticated) {
    next({ name: 'dashboard' })
    return
  }

  // Check admin requirement
  if (to.meta.requiresAdmin && !authStore.isAdmin) {
    next({ name: 'dashboard' })
    return
  }

  next()
})

export default router
