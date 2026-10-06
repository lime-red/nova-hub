<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useClientsStore, type FtnAddressInfo } from '@/stores/clients'
import { useAuthStore } from '@/stores/auth'
import AppLayout from '@/components/AppLayout.vue'
import { clientsApi, leaguesApi } from '@/services/api'
import { useCopy } from '@/clipboard'

const route = useRoute()
const router = useRouter()
const clientsStore = useClientsStore()
const authStore = useAuthStore()

const clientId = computed(() => Number(route.params.id))

// The last three lines of this BBS's nodes.dat entry. Any of them may be blank.
const clientLocation = computed(() => {
  const client = clientsStore.currentClient
  if (!client) return 'Not set'
  const parts = [client.city, client.state, client.country].filter(Boolean)
  return parts.length ? parts.join(', ') : 'Not set'
})
const showEditModal = ref(false)
const showDeleteModal = ref(false)
const showSecretModal = ref(false)
const editBbsName = ref('')
// Location lines for this BBS's entry in generated nodelists. Blank is valid --
// nodes.dat carries an empty line rather than omitting the field.
const editCity = ref('')
const editState = ref('')
const editCountry = ref('')
const newSecret = ref('')

// FTN addresses belong to the BBS; a league membership picks one of them.
// Admin only: renumbering one moves every league that uses it.
const newAddress = ref('')
const editingAddressId = ref<number | null>(null)
const editAddressText = ref('')
const addressError = ref<string | null>(null)

// Claim links: how a sysop gets credentials without a secret going over chat.
// The link itself is shown once, here, right after it is issued.
interface ClaimLinkStatus {
  state: 'ready' | 'used' | 'superseded' | 'expired'
  issued_by?: string | null
  issued_at: string
  expires_at: string
  used_at?: string | null
  used_ip?: string | null
}
const claimStatus = ref<ClaimLinkStatus | null>(null)
const issuedLink = ref<{ url: string; expires_at: string; superseded: number } | null>(null)
const claimError = ref<string | null>(null)
const { copy, label: copyLabel } = useCopy()

async function loadClaimStatus() {
  if (!authStore.isAdmin) return
  try {
    claimStatus.value = (await clientsApi.getClaimLink(clientId.value)).data
  } catch {
    claimStatus.value = null
  }
}

async function handleIssueClaimLink() {
  claimError.value = null
  try {
    issuedLink.value = (await clientsApi.issueClaimLink(clientId.value)).data
    await loadClaimStatus()
  } catch (e: any) {
    claimError.value = e.response?.data?.detail || 'Could not issue a claim link'
  }
}

function claimStatusText(s: ClaimLinkStatus): string {
  const at = (iso?: string | null) => (iso ? new Date(iso).toLocaleString() : '')
  switch (s.state) {
    case 'ready':
      return `Waiting to be claimed; expires ${at(s.expires_at)}`
    case 'used':
      return `Claimed ${at(s.used_at)} from ${s.used_ip}`
    case 'superseded':
      return 'Replaced by a newer link'
    default:
      return `Expired ${at(s.expires_at)} without being claimed`
  }
}

onMounted(async () => {
  await clientsStore.loadClient(clientId.value)
  await loadClaimStatus()
})

function openEditModal() {
  if (clientsStore.currentClient) {
    editBbsName.value = clientsStore.currentClient.bbs_name
    editCity.value = clientsStore.currentClient.city || ''
    editState.value = clientsStore.currentClient.state || ''
    editCountry.value = clientsStore.currentClient.country || ''
    clientsStore.clearError()
    showEditModal.value = true
  }
}

async function handleEdit() {
  const success = await clientsStore.updateClient(clientId.value, {
    bbs_name: editBbsName.value,
    city: editCity.value,
    state: editState.value,
    country: editCountry.value
  })
  if (success) {
    showEditModal.value = false
  }
}

async function handleToggleActive() {
  if (clientsStore.currentClient) {
    await clientsStore.updateClient(clientId.value, {
      is_active: !clientsStore.currentClient.is_active
    })
  }
}

async function handleRegenerateSecret() {
  const secret = await clientsStore.regenerateSecret(clientId.value)
  if (secret) {
    newSecret.value = secret
    showSecretModal.value = true
  }
}

async function handleDelete() {
  const success = await clientsStore.deleteClient(clientId.value)
  if (success) {
    router.push('/clients')
  }
}

async function handleAddAddress() {
  if (!newAddress.value.trim()) return
  addressError.value = null
  if (await clientsStore.addFtnAddress(clientId.value, newAddress.value)) {
    newAddress.value = ''
  } else {
    addressError.value = clientsStore.error
  }
}

function startEditAddress(address: FtnAddressInfo) {
  addressError.value = null
  editingAddressId.value = address.id
  editAddressText.value = address.address
}

async function handleSaveAddress() {
  if (editingAddressId.value === null) return
  addressError.value = null
  if (await clientsStore.updateFtnAddress(clientId.value, editingAddressId.value, editAddressText.value)) {
    editingAddressId.value = null
  } else {
    addressError.value = clientsStore.error
  }
}

async function handleRemoveAddress(address: FtnAddressInfo) {
  if (!confirm(`Remove ${address.address} from this BBS?`)) return
  addressError.value = null
  if (!(await clientsStore.deleteFtnAddress(clientId.value, address.id))) {
    addressError.value = clientsStore.error
  }
}

</script>

<template>
  <AppLayout>
    <div class="page">
      <!-- Loading -->
      <div v-if="clientsStore.loading && !clientsStore.currentClient" class="loading-state">
        <div class="spinner"></div>
        <p>Loading client...</p>
      </div>

      <!-- Error -->
      <div v-else-if="clientsStore.error && !clientsStore.currentClient" class="alert alert-error">
        {{ clientsStore.error }}
        <router-link to="/clients" class="btn btn-sm btn-secondary mt-2">Back to Clients</router-link>
      </div>

      <!-- Client Details -->
      <template v-else-if="clientsStore.currentClient">
        <header class="page-header">
          <div>
            <div class="breadcrumb">
              <router-link to="/clients">Clients</router-link>
              <span>/</span>
              <span>{{ clientsStore.currentClient.bbs_name }}</span>
            </div>
            <h1>{{ clientsStore.currentClient.bbs_name }}</h1>
            <p class="text-muted font-mono">{{ clientsStore.currentClient.client_id }}</p>
          </div>
          <div v-if="authStore.isAdmin" class="actions">
            <button class="btn btn-secondary" @click="openEditModal">Edit</button>
            <button
              class="btn"
              :class="clientsStore.currentClient.is_active ? 'btn-secondary' : 'btn-primary'"
              @click="handleToggleActive"
            >
              {{ clientsStore.currentClient.is_active ? 'Disable' : 'Enable' }}
            </button>
            <button class="btn btn-danger" @click="showDeleteModal = true">Delete</button>
          </div>
        </header>

        <div class="content-grid">
          <!-- Info Card -->
          <div class="card">
            <div class="card-header">
              <h3>Client Information</h3>
            </div>
            <div class="card-body">
              <dl class="info-list">
                <div class="info-item">
                  <dt>Status</dt>
                  <dd>
                    <span
                      class="badge"
                      :class="clientsStore.currentClient.is_active ? 'badge-success' : 'badge-danger'"
                    >
                      {{ clientsStore.currentClient.is_active ? 'Active' : 'Inactive' }}
                    </span>
                  </dd>
                </div>
                <div class="info-item">
                  <dt>Created</dt>
                  <dd>{{ clientsStore.currentClient.created_at || 'Unknown' }}</dd>
                </div>
                <div class="info-item">
                  <dt>Last Seen</dt>
                  <dd>{{ clientsStore.currentClient.stats.last_seen || 'Never' }}</dd>
                </div>
                <div class="info-item">
                  <dt>Location</dt>
                  <dd>{{ clientLocation }}</dd>
                </div>
              </dl>

              <div v-if="authStore.isAdmin" class="credential-actions mt-4">
                <button class="btn btn-primary btn-sm" @click="handleIssueClaimLink">
                  Issue Claim Link
                </button>
                <button class="btn btn-secondary btn-sm" @click="handleRegenerateSecret">
                  Regenerate Secret
                </button>
              </div>
              <p v-if="authStore.isAdmin" class="form-hint mt-2">
                <template v-if="claimStatus">Last claim link: {{ claimStatusText(claimStatus) }}.</template>
                <template v-else>
                  Send the sysop a claim link rather than a secret: it works once and expires in 72 hours.
                </template>
              </p>
              <div v-if="claimError" class="alert alert-danger mt-2">{{ claimError }}</div>
            </div>
          </div>

          <!-- Stats Card -->
          <div class="card">
            <div class="card-header">
              <h3>Statistics</h3>
            </div>
            <div class="card-body">
              <div class="stats-grid">
                <div class="stat">
                  <div class="stat-value">{{ clientsStore.currentClient.stats.sent_24h }}</div>
                  <div class="stat-label">Sent (24h)</div>
                </div>
                <div class="stat">
                  <div class="stat-value">{{ clientsStore.currentClient.stats.total_sent }}</div>
                  <div class="stat-label">Total Sent</div>
                </div>
                <div class="stat">
                  <div class="stat-value">{{ clientsStore.currentClient.stats.received_24h }}</div>
                  <div class="stat-label">Received (24h)</div>
                </div>
                <div class="stat">
                  <div class="stat-value">{{ clientsStore.currentClient.stats.total_received }}</div>
                  <div class="stat-label">Total Received</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- FTN Addresses -->
        <div class="card mt-4">
          <div class="card-header">
            <h3>FTN Addresses</h3>
          </div>
          <div class="card-body" style="padding: 0;">
            <div v-if="addressError" class="alert alert-error address-alert">{{ addressError }}</div>
            <table class="table">
              <thead>
                <tr>
                  <th>Address</th>
                  <th>Used In</th>
                  <th v-if="authStore.isAdmin"></th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="!clientsStore.currentClient.ftn_addresses?.length">
                  <td :colspan="authStore.isAdmin ? 3 : 2" class="text-center text-muted" style="padding: 2rem;">
                    No FTN addresses assigned
                  </td>
                </tr>
                <tr v-for="address in clientsStore.currentClient.ftn_addresses" :key="address.id">
                  <td class="font-mono">
                    <form
                      v-if="editingAddressId === address.id"
                      class="address-form"
                      @submit.prevent="handleSaveAddress"
                    >
                      <input v-model="editAddressText" type="text" class="font-mono" required />
                      <button type="submit" class="btn btn-sm btn-primary">Save</button>
                      <button type="button" class="btn btn-sm btn-secondary" @click="editingAddressId = null">
                        Cancel
                      </button>
                    </form>
                    <template v-else>{{ address.address }}</template>
                  </td>
                  <td>
                    <span v-if="!address.leagues.length" class="text-muted">Not used</span>
                    <span v-else class="font-mono">{{ address.leagues.join(', ') }}</span>
                  </td>
                  <td v-if="authStore.isAdmin" class="actions">
                    <button
                      class="btn btn-sm btn-secondary"
                      :disabled="editingAddressId !== null"
                      @click="startEditAddress(address)"
                    >
                      Renumber
                    </button>
                    <button
                      class="btn btn-sm btn-danger"
                      :disabled="address.leagues.length > 0"
                      :title="address.leagues.length ? 'In use; move those memberships first' : ''"
                      @click="handleRemoveAddress(address)"
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
            <form v-if="authStore.isAdmin" class="address-form address-add" @submit.prevent="handleAddAddress">
              <input
                v-model="newAddress"
                type="text"
                class="font-mono"
                placeholder="zone:net/node, e.g. 135:135/21"
                pattern="\d+:\d+/\d+"
              />
              <button type="submit" class="btn btn-sm btn-primary" :disabled="!newAddress.trim()">
                Assign Address
              </button>
            </form>
            <p class="form-hint address-hint">
              An address belongs to one BBS across the whole hub. Renumbering one
              changes it in every league listed, from each league's next nodelist.
            </p>
          </div>
        </div>

        <!-- League Memberships -->
        <div class="card mt-4">
          <div class="card-header">
            <h3>League Memberships</h3>
          </div>
          <div class="card-body" style="padding: 0;">
            <table class="table">
              <thead>
                <tr>
                  <th>League</th>
                  <th>BBS Index</th>
                  <th>Fidonet Address</th>
                  <th>Nodelist</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="!clientsStore.currentClient.league_memberships?.length">
                  <td colspan="4" class="text-center text-muted" style="padding: 2rem;">
                    Not a member of any leagues
                  </td>
                </tr>
                <tr v-for="membership in clientsStore.currentClient.league_memberships" :key="membership.league_id">
                  <td>
                    <router-link :to="`/leagues/${membership.league_id}`">
                      {{ membership.league_name }} ({{ membership.full_id }})
                    </router-link>
                  </td>
                  <td class="font-mono">{{ String(membership.bbs_index).padStart(2, '0') }}</td>
                  <td class="font-mono">{{ membership.fidonet_address || '-' }}</td>
                  <td>
                    <a
                      v-if="membership.nodelist_filename"
                      class="font-mono"
                      :href="leaguesApi.nodelistUrl(membership.league_id)"
                      download
                    >{{ membership.nodelist_filename }}</a>
                    <span v-else class="text-muted">Not generated yet</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Packet History -->
        <div class="card mt-4">
          <div class="card-header">
            <h3>Recent Packets</h3>
          </div>
          <div class="card-body" style="padding: 0;">
            <table class="table">
              <thead>
                <tr>
                  <th>Filename</th>
                  <th>Direction</th>
                  <th>League</th>
                  <th>Route</th>
                  <th>Uploaded</th>
                  <th>Processed</th>
                  <th>Retrieved</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="clientsStore.currentClient.packets.length === 0">
                  <td colspan="7" class="text-center text-muted" style="padding: 2rem;">
                    No packets found
                  </td>
                </tr>
                <tr v-for="packet in clientsStore.currentClient.packets" :key="packet.filename">
                  <td class="font-mono">{{ packet.filename }}</td>
                  <td>
                    <span class="badge" :class="packet.direction === 'received' ? 'badge-success' : packet.direction === 'queued' ? 'badge-warning' : 'badge-info'">
                      {{ packet.direction === 'received' ? 'Received' : packet.direction === 'queued' ? 'Queued' : 'Sent' }}
                    </span>
                  </td>
                  <td>{{ packet.league_name }}</td>
                  <td class="font-mono">{{ packet.source }} → {{ packet.dest }}</td>
                  <td class="text-muted">{{ packet.timestamp }}</td>
                  <td class="text-muted">
                    <router-link v-if="packet.processing_run_id && packet.processed_at" :to="`/processing/${packet.processing_run_id}`">
                      {{ packet.processed_at }}
                    </router-link>
                    <span v-else>{{ packet.processed_at || '-' }}</span>
                  </td>
                  <td class="text-muted">{{ packet.retrieved_at || '-' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </template>

      <!-- Edit Modal -->
      <div v-if="showEditModal" class="modal-overlay" @click.self="showEditModal = false">
        <div class="modal">
          <div class="modal-header">
            <h3>Edit Client</h3>
            <button class="modal-close" @click="showEditModal = false">&times;</button>
          </div>
          <form @submit.prevent="handleEdit">
            <div class="modal-body">
              <div v-if="clientsStore.error" class="alert alert-error mb-4">
                {{ clientsStore.error }}
              </div>
              <div class="form-group">
                <label for="editBbsName">BBS Name</label>
                <input id="editBbsName" v-model="editBbsName" type="text" required />
                <small class="form-hint">
                  Appears in generated nodelists. The games match nodes by index,
                  not by name, so a rename is safe.
                </small>
              </div>
              <div class="form-group">
                <label for="editCity">City</label>
                <input id="editCity" v-model="editCity" type="text" />
              </div>
              <div class="form-group">
                <label for="editState">State / Province</label>
                <input id="editState" v-model="editState" type="text" />
              </div>
              <div class="form-group">
                <label for="editCountry">Country</label>
                <input id="editCountry" v-model="editCountry" type="text" />
                <small class="form-hint">
                  City, state and country are the last three lines of this BBS's
                  nodes.dat entry. Leave blank to write empty lines.
                </small>
              </div>
            </div>
            <div class="modal-footer">
              <button type="button" class="btn btn-secondary" @click="showEditModal = false">
                Cancel
              </button>
              <button type="submit" class="btn btn-primary" :disabled="clientsStore.loading">
                Save Changes
              </button>
            </div>
          </form>
        </div>
      </div>

      <!-- Delete Modal -->
      <div v-if="showDeleteModal" class="modal-overlay" @click.self="showDeleteModal = false">
        <div class="modal">
          <div class="modal-header">
            <h3>Delete Client</h3>
            <button class="modal-close" @click="showDeleteModal = false">&times;</button>
          </div>
          <div class="modal-body">
            <p>Are you sure you want to delete <strong>{{ clientsStore.currentClient?.bbs_name }}</strong>?</p>
            <p class="text-muted mt-2">This action cannot be undone.</p>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-secondary" @click="showDeleteModal = false">
              Cancel
            </button>
            <button type="button" class="btn btn-danger" @click="handleDelete" :disabled="clientsStore.loading">
              Delete Client
            </button>
          </div>
        </div>
      </div>

      <!-- Claim Link Modal -->
      <div v-if="issuedLink" class="modal-overlay">
        <div class="modal">
          <div class="modal-header">
            <h3>Claim Link</h3>
          </div>
          <div class="modal-body">
            <p class="mb-4">
              Send this to the sysop of {{ clientsStore.currentClient?.bbs_name }}. Opening it shows their
              credentials and a ready-made config file, once. It expires
              {{ new Date(issuedLink.expires_at).toLocaleString() }}.
            </p>
            <div v-if="issuedLink.superseded" class="alert alert-warning mb-4">
              The previous unclaimed link for this BBS no longer works.
            </div>
            <p class="form-hint mb-4">
              Nothing changes for the BBS until the link is claimed. Claiming creates a new secret, which
              replaces the current one.
            </p>
            <div class="secret-display">
              <input type="text" :value="issuedLink.url" readonly class="font-mono" />
              <button type="button" class="btn btn-secondary" @click="copy('link', issuedLink.url, $event)">
                {{ copyLabel('link') }}
              </button>
            </div>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-primary" @click="issuedLink = null">Done</button>
          </div>
        </div>
      </div>

      <!-- Secret Modal -->
      <div v-if="showSecretModal" class="modal-overlay">
        <div class="modal">
          <div class="modal-header">
            <h3>New Client Secret</h3>
          </div>
          <div class="modal-body">
            <div class="alert alert-warning mb-4">
              Save this secret now! It will not be shown again.
            </div>
            <div class="form-group">
              <label>Client Secret</label>
              <div class="secret-display">
                <input type="text" :value="newSecret" readonly class="font-mono" />
                <button type="button" class="btn btn-secondary" @click="copy('secret', newSecret, $event)">
                  {{ copyLabel('secret') }}
                </button>
              </div>
            </div>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-primary" @click="showSecretModal = false">
              I've Saved the Secret
            </button>
          </div>
        </div>
      </div>
    </div>
  </AppLayout>
</template>

<style scoped>
.credential-actions {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.address-form {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.address-form input {
  max-width: 14rem;
}

.address-add {
  padding: 1rem 1.5rem 0;
}

.address-hint {
  padding: 0.5rem 1.5rem 1rem;
  margin: 0;
}

.address-alert {
  margin: 1rem 1.5rem 0;
}

table .actions {
  justify-content: flex-end;
}

.page {
  max-width: 1200px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 1.5rem;
}

.page-header h1 {
  margin: 0.25rem 0;
}

.breadcrumb {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
  color: var(--color-text-muted);
  margin-bottom: 0.25rem;
}

.breadcrumb a {
  color: var(--color-primary);
}

.actions {
  display: flex;
  gap: 0.5rem;
}

.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 4rem;
  gap: 1rem;
}

.content-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 1.5rem;
}

.info-list {
  margin: 0;
}

.info-item {
  display: flex;
  justify-content: space-between;
  padding: 0.75rem 0;
  border-bottom: 1px solid var(--color-border);
}

.info-item:last-child {
  border-bottom: none;
}

.info-item dt {
  color: var(--color-text-muted);
}

.info-item dd {
  margin: 0;
  font-weight: 500;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 1rem;
}

.stat {
  text-align: center;
  padding: 1rem;
  background: var(--color-bg);
  border-radius: var(--radius-md);
}

.stat-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--color-primary);
}

.stat-label {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin-top: 0.25rem;
}

.mt-4 {
  margin-top: 1rem;
}

.mb-4 {
  margin-bottom: 1rem;
}

.form-group {
  margin-bottom: 1rem;
}

.form-group label {
  display: block;
  font-weight: 500;
  margin-bottom: 0.375rem;
}

.form-hint {
  display: block;
  margin-top: 0.375rem;
  color: var(--color-text-muted);
  font-size: 0.8125rem;
  line-height: 1.4;
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
</style>
