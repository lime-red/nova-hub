<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useClientsStore, type ClientCreated } from '@/stores/clients'
import { useAuthStore } from '@/stores/auth'
import AppLayout from '@/components/AppLayout.vue'

const clientsStore = useClientsStore()
const authStore = useAuthStore()

const showCreateModal = ref(false)
const showSecretModal = ref(false)
const newBbsName = ref('')
const newClientId = ref('')
const createdClient = ref<ClientCreated | null>(null)

onMounted(async () => {
  await clientsStore.loadClients()
})

function openCreateModal() {
  newBbsName.value = ''
  newClientId.value = ''
  clientsStore.clearError()
  showCreateModal.value = true
}

async function handleCreate() {
  if (!newBbsName.value || !newClientId.value) return

  const result = await clientsStore.createClient(newBbsName.value, newClientId.value)
  if (result) {
    createdClient.value = result
    showCreateModal.value = false
    showSecretModal.value = true
  }
}

function closeSecretModal() {
  showSecretModal.value = false
  createdClient.value = null
}

async function copySecret() {
  if (createdClient.value) {
    await navigator.clipboard.writeText(createdClient.value.client_secret)
  }
}

async function toggleActive(clientId: number, currentActive: boolean) {
  await clientsStore.updateClient(clientId, { is_active: !currentActive })
}
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="page-header">
        <div>
          <h1>Clients</h1>
          <p class="text-muted">Manage BBS clients connected to Nova Hub</p>
        </div>
        <button v-if="authStore.isAdmin" class="btn btn-primary" @click="openCreateModal">
          Add Client
        </button>
      </header>

      <!-- Loading -->
      <div v-if="clientsStore.loading && clientsStore.clients.length === 0" class="loading-state">
        <div class="spinner"></div>
        <p>Loading clients...</p>
      </div>

      <!-- Error -->
      <div v-else-if="clientsStore.error" class="alert alert-error">
        {{ clientsStore.error }}
        <button class="btn btn-sm btn-secondary mt-2" @click="clientsStore.loadClients">Retry</button>
      </div>

      <!-- Clients Table -->
      <div v-else class="card">
        <div class="card-body" style="padding: 0;">
          <table class="table">
            <thead>
              <tr>
                <th>BBS Name</th>
                <th>Client ID</th>
                <th>Status</th>
                <th>Last Seen</th>
                <th>Sent (24h)</th>
                <th>Received (24h)</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="clientsStore.clients.length === 0">
                <td colspan="7" class="text-center text-muted" style="padding: 2rem;">
                  No clients configured
                </td>
              </tr>
              <tr v-for="client in clientsStore.clients" :key="client.id">
                <td>
                  <router-link :to="`/clients/${client.id}`" class="client-name">
                    {{ client.bbs_name }}
                  </router-link>
                </td>
                <td class="font-mono">{{ client.client_id }}</td>
                <td>
                  <span class="badge" :class="client.is_active ? 'badge-success' : 'badge-danger'">
                    {{ client.is_active ? 'Active' : 'Inactive' }}
                  </span>
                </td>
                <td class="text-muted">{{ client.last_seen || 'Never' }}</td>
                <td>{{ client.packets_sent_24h }}</td>
                <td>{{ client.packets_received_24h }}</td>
                <td class="actions">
                  <router-link :to="`/clients/${client.id}`" class="btn btn-sm btn-secondary">
                    View
                  </router-link>
                  <button
                    v-if="authStore.isAdmin"
                    class="btn btn-sm"
                    :class="client.is_active ? 'btn-secondary' : 'btn-primary'"
                    @click="toggleActive(client.id, client.is_active)"
                  >
                    {{ client.is_active ? 'Disable' : 'Enable' }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Create Modal -->
      <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
        <div class="modal">
          <div class="modal-header">
            <h3>Add New Client</h3>
            <button class="modal-close" @click="showCreateModal = false">&times;</button>
          </div>
          <form @submit.prevent="handleCreate">
            <div class="modal-body">
              <div v-if="clientsStore.error" class="alert alert-error mb-4">
                {{ clientsStore.error }}
              </div>
              <div class="form-group">
                <label for="bbsName">BBS Name</label>
                <input
                  id="bbsName"
                  v-model="newBbsName"
                  type="text"
                  placeholder="e.g., My Awesome BBS"
                  required
                />
              </div>
              <div class="form-group">
                <label for="clientId">Client ID</label>
                <input
                  id="clientId"
                  v-model="newClientId"
                  type="text"
                  placeholder="e.g., my_bbs"
                  pattern="[a-z0-9_]+"
                  required
                />
                <small class="text-muted">Lowercase letters, numbers, and underscores only</small>
              </div>
            </div>
            <div class="modal-footer">
              <button type="button" class="btn btn-secondary" @click="showCreateModal = false">
                Cancel
              </button>
              <button
                type="submit"
                class="btn btn-primary"
                :disabled="clientsStore.loading || !newBbsName || !newClientId"
              >
                Create Client
              </button>
            </div>
          </form>
        </div>
      </div>

      <!-- Secret Modal -->
      <div v-if="showSecretModal" class="modal-overlay">
        <div class="modal">
          <div class="modal-header">
            <h3>Client Created</h3>
          </div>
          <div class="modal-body">
            <div class="alert alert-warning mb-4">
              Save this secret now! It will not be shown again.
            </div>
            <div class="form-group">
              <label>Client ID</label>
              <input type="text" :value="createdClient?.client_id" readonly class="font-mono" />
            </div>
            <div class="form-group">
              <label>Client Secret</label>
              <div class="secret-display">
                <input type="text" :value="createdClient?.client_secret" readonly class="font-mono" />
                <button type="button" class="btn btn-secondary" @click="copySecret">Copy</button>
              </div>
            </div>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-primary" @click="closeSecretModal">
              I've Saved the Secret
            </button>
          </div>
        </div>
      </div>
    </div>
  </AppLayout>
</template>

<style scoped>
.page {
  max-width: 1400px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 1.5rem;
}

.page-header h1 {
  margin-bottom: 0.25rem;
}

.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 4rem;
  gap: 1rem;
}

.client-name {
  font-weight: 500;
}

.actions {
  display: flex;
  gap: 0.5rem;
  justify-content: flex-end;
}

.form-group {
  margin-bottom: 1rem;
}

.form-group label {
  display: block;
  font-weight: 500;
  margin-bottom: 0.375rem;
}

.form-group small {
  display: block;
  margin-top: 0.25rem;
}

.secret-display {
  display: flex;
  gap: 0.5rem;
}

.secret-display input {
  flex: 1;
}

/* Modal styles */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal {
  background: var(--color-surface);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  width: 100%;
  max-width: 480px;
  max-height: 90vh;
  overflow-y: auto;
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.5rem;
  border-bottom: 1px solid var(--color-border);
}

.modal-header h3 {
  margin: 0;
}

.modal-close {
  background: none;
  border: none;
  font-size: 1.5rem;
  cursor: pointer;
  color: var(--color-text-muted);
  line-height: 1;
}

.modal-close:hover {
  color: var(--color-text);
}

.modal-body {
  padding: 1.5rem;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 0.75rem;
  padding: 1rem 1.5rem;
  border-top: 1px solid var(--color-border);
}

.mb-4 {
  margin-bottom: 1rem;
}
</style>
