<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { systemApi } from '@/services/api'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const isSidebarOpen = ref(true)

// What build is running. Shown in the sidebar footer because the login screen
// is otherwise the only place it appears, and reading it there means logging
// out. Failure is silent -- a missing version is not worth an error banner.
const build = ref<{
  version: string
  revision: string | null
  released_at: string | null
  dirty: boolean
} | null>(null)

onMounted(async () => {
  try {
    const response = await systemApi.version()
    build.value = response.data
  } catch {
    build.value = null
  }
})

const buildLabel = computed(() => {
  if (!build.value) return ''
  const revision = build.value.revision ? ` · ${build.value.revision}` : ''
  return `v${build.value.version}${revision}${build.value.dirty ? '+' : ''}`
})

const buildTitle = computed(() => {
  if (!build.value) return ''
  const parts = [`Version ${build.value.version}`]
  if (build.value.revision) parts.push(`commit ${build.value.revision}`)
  if (build.value.released_at) parts.push(`released ${build.value.released_at}`)
  if (build.value.dirty) parts.push('working tree has uncommitted changes')
  return parts.join(' — ')
})

const navigation = computed(() => [
  { name: 'Dashboard', path: '/dashboard', icon: 'dashboard' },
  { name: 'Clients', path: '/clients', icon: 'clients' },
  { name: 'Leagues', path: '/leagues', icon: 'leagues' },
  { name: 'Processing', path: '/processing', icon: 'processing' },
  { name: 'Alerts', path: '/alerts', icon: 'alerts' },
])

const adminNavigation = computed(() => [
  { name: 'Users', path: '/admin/users', icon: 'users' },
])

function isActive(path: string): boolean {
  return route.path.startsWith(path)
}

async function handleLogout() {
  await authStore.logout()
  router.push('/login')
}

function toggleSidebar() {
  isSidebarOpen.value = !isSidebarOpen.value
}
</script>

<template>
  <div class="app-layout" :class="{ 'sidebar-collapsed': !isSidebarOpen }">
    <!-- Sidebar -->
    <aside class="sidebar">
      <div class="sidebar-header">
        <h1 class="sidebar-logo">Nova Hub</h1>
        <button class="sidebar-toggle" @click="toggleSidebar">
          <span class="toggle-icon">{{ isSidebarOpen ? '<<' : '>>' }}</span>
        </button>
      </div>

      <nav class="sidebar-nav">
        <ul class="nav-list">
          <li v-for="item in navigation" :key="item.path">
            <router-link
              :to="item.path"
              class="nav-link"
              :class="{ active: isActive(item.path) }"
            >
              <span class="nav-icon">{{ item.icon.charAt(0).toUpperCase() }}</span>
              <span v-if="isSidebarOpen" class="nav-text">{{ item.name }}</span>
            </router-link>
          </li>
        </ul>

        <div v-if="authStore.isAdmin" class="nav-section">
          <span v-if="isSidebarOpen" class="nav-section-title">Admin</span>
          <ul class="nav-list">
            <li v-for="item in adminNavigation" :key="item.path">
              <router-link
                :to="item.path"
                class="nav-link"
                :class="{ active: isActive(item.path) }"
              >
                <span class="nav-icon">{{ item.icon.charAt(0).toUpperCase() }}</span>
                <span v-if="isSidebarOpen" class="nav-text">{{ item.name }}</span>
              </router-link>
            </li>
          </ul>
        </div>
      </nav>

      <div class="sidebar-footer">
        <div class="sidebar-footer-row">
          <div class="user-info" v-if="isSidebarOpen">
            <span class="user-name">{{ authStore.username }}</span>
            <span class="user-role">{{ authStore.isAdmin ? 'Admin' : 'User' }}</span>
          </div>
          <button class="btn btn-secondary btn-sm" @click="handleLogout">
            {{ isSidebarOpen ? 'Logout' : 'X' }}
          </button>
        </div>
        <div v-if="build && isSidebarOpen" class="build-info" :title="buildTitle">
          <span class="build-version">{{ buildLabel }}</span>
          <span v-if="build.released_at" class="build-date">{{ build.released_at }}</span>
        </div>
      </div>
    </aside>

    <!-- Main Content -->
    <main class="main-content">
      <slot></slot>
    </main>
  </div>
</template>

<style scoped>
.app-layout {
  display: flex;
  min-height: 100vh;
}

.sidebar {
  width: 240px;
  background-color: var(--color-bg-dark);
  color: var(--color-text-light);
  display: flex;
  flex-direction: column;
  transition: width 0.2s ease;
}

.sidebar-collapsed .sidebar {
  width: 60px;
}

.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem;
  border-bottom: 1px solid var(--color-surface-dark);
}

.sidebar-logo {
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--color-primary);
}

.sidebar-collapsed .sidebar-logo {
  display: none;
}

.sidebar-toggle {
  background: none;
  border: none;
  color: var(--color-text-light);
  cursor: pointer;
  padding: 0.25rem;
  opacity: 0.7;
}

.sidebar-toggle:hover {
  opacity: 1;
}

.toggle-icon {
  font-family: var(--font-mono);
  font-size: 0.75rem;
}

.sidebar-nav {
  flex: 1;
  padding: 1rem 0;
  overflow-y: auto;
}

.nav-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.nav-link {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.75rem 1rem;
  color: var(--color-text-light);
  text-decoration: none;
  opacity: 0.8;
  transition: all 0.15s;
}

.nav-link:hover {
  opacity: 1;
  background-color: var(--color-surface-dark);
}

.nav-link.active {
  opacity: 1;
  background-color: var(--color-primary);
}

.nav-icon {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: var(--color-surface-dark);
  border-radius: var(--radius-md);
  font-weight: 600;
  font-size: 0.875rem;
}

.nav-link.active .nav-icon {
  background-color: rgba(255, 255, 255, 0.2);
}

.nav-text {
  font-weight: 500;
}

.nav-section {
  margin-top: 1rem;
  padding-top: 1rem;
  border-top: 1px solid var(--color-surface-dark);
}

.nav-section-title {
  display: block;
  padding: 0.5rem 1rem;
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  opacity: 0.5;
}

.sidebar-footer {
  padding: 1rem;
  border-top: 1px solid var(--color-surface-dark);
  display: flex;
  flex-direction: column;
  gap: 0.625rem;
}

.sidebar-footer-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}

/* Build identity. Quiet by design -- it is reference information, not status,
   and should not compete with the navigation above it. */
.build-info {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.5rem;
  font-size: 0.6875rem;
  line-height: 1.2;
  color: var(--color-text-muted);
  cursor: default;
}

.build-version {
  font-family: var(--font-mono, ui-monospace, monospace);
  white-space: nowrap;
}

.build-date {
  white-space: nowrap;
  opacity: 0.8;
}

.user-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.user-name {
  font-weight: 500;
  font-size: 0.875rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.user-role {
  font-size: 0.75rem;
  opacity: 0.7;
}

.sidebar-collapsed .user-info {
  display: none;
}

.main-content {
  flex: 1;
  padding: 1.5rem;
  overflow-y: auto;
  background-color: var(--color-bg);
}
</style>
