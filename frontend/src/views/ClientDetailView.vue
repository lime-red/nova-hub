<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useClientsStore, type FtnAddressInfo } from '@/stores/clients'
import { useAuthStore } from '@/stores/auth'
import AppLayout from '@/components/AppLayout.vue'
import LeagueStatusTable from '@/components/LeagueStatusTable.vue'
import {
  applicationsApi,
  clientsApi,
  usersApi,
  type Application,
  type JoinableLeague,
  type LeagueStatusResponse,
} from '@/services/api'
import type { User } from '@/stores/auth'
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
// A routine rotation keeps the old secret a day, so the BBS isn't cut off
// before its config is edited; a leaked one is stopped at once.
const overlapHours = ref(24)
const newSecretOldUntil = ref<string | null>(null)
const secretError = ref('')

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
  bound_to?: string | null
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

// '' for an unbound link (a sysop with no account), else one of the owners
const claimFor = ref<number | ''>('')

async function handleIssueClaimLink() {
  claimError.value = null
  try {
    issuedLink.value = (
      await clientsApi.issueClaimLink(clientId.value, claimFor.value === '' ? undefined : claimFor.value)
    ).data
    await loadClaimStatus()
  } catch (e: any) {
    claimError.value = e.response?.data?.detail || 'Could not issue a claim link'
  }
}

function claimStatusText(s: ClaimLinkStatus): string {
  const at = (iso?: string | null) => (iso ? new Date(iso).toLocaleString() : '')
  switch (s.state) {
    case 'ready':
      return `Waiting to be claimed${s.bound_to ? ` by ${s.bound_to}` : ''}; expires ${at(s.expires_at)}`
    case 'used':
      return `Claimed ${at(s.used_at)} from ${s.used_ip}`
    case 'superseded':
      return 'Replaced by a newer link'
    default:
      return `Expired ${at(s.expires_at)} without being claimed`
  }
}

// A sysop collecting the credentials the admin approved for them. The
// console can't show the link the admin saw (only its hash is kept), so this
// swaps it for a fresh one bound to the same account, and opens it.
const collecting = ref(false)
async function handleCollect() {
  collecting.value = true
  claimError.value = null
  try {
    const url = (await clientsApi.collectClaimLink(clientId.value)).data.url
    router.push(new URL(url).pathname)
  } catch (e: any) {
    claimError.value = e.response?.data?.detail || 'Could not collect the credentials'
  } finally {
    collecting.value = false
  }
}

// Onboarding's Verify step: approved -> claimed -> first sign-in -> first upload -> active
const STAGES = [
  { key: 'approved', label: 'Approved' },
  { key: 'claimed', label: 'Credentials claimed' },
  { key: 'first_token', label: 'First sign-in' },
  { key: 'first_upload', label: 'First upload' },
  { key: 'active', label: 'Active' },
] as const
const stageIndex = computed(() =>
  STAGES.findIndex((s) => s.key === (clientsStore.currentClient?.stage ?? 'approved'))
)
const STAGE_HINT: Record<string, string> = {
  approved: 'Waiting for the sysop to claim the credentials.',
  claimed: 'Credentials claimed; the client has not signed in yet.',
  first_token: 'The client signs in but has not uploaded a packet yet. Usually game paths.',
  first_upload: 'Has exchanged packets, but has not been seen in the last two days.',
}

// A sysop asking for more leagues for this BBS. The admin approves it, like a join.
const myRequests = ref<Application[]>([])
const openLeagues = ref<JoinableLeague[]>([])
const requestLeagueIds = ref<number[]>([])
const requestNote = ref('')
const requestError = ref<string | null>(null)
const pendingRequest = computed(() =>
  myRequests.value.find((a) => a.kind === 'leagues' && a.client_id === clientId.value && a.status === 'pending')
)
const lastDecided = computed(() =>
  myRequests.value.find((a) => a.kind === 'leagues' && a.client_id === clientId.value && a.status !== 'pending')
)
const requestableLeagues = computed(() => {
  const member = new Set(
    (clientsStore.currentClient?.league_memberships ?? []).map((m) => m.league_id)
  )
  return openLeagues.value.filter((l) => !member.has(l.id))
})

async function loadRequests() {
  if (authStore.isAdmin) return
  try {
    const [mine, open] = await Promise.all([applicationsApi.mine(), applicationsApi.leagues()])
    myRequests.value = mine.data
    openLeagues.value = open.data
  } catch {
    myRequests.value = []
  }
}

async function handleRequestLeagues() {
  requestError.value = null
  if (!requestLeagueIds.value.length) {
    requestError.value = 'Choose at least one league.'
    return
  }
  try {
    await applicationsApi.apply({
      kind: 'leagues',
      client_id: clientId.value,
      league_ids: requestLeagueIds.value,
      note: requestNote.value.trim() || undefined,
    })
    requestLeagueIds.value = []
    requestNote.value = ''
    await loadRequests()
  } catch (e: any) {
    requestError.value = e.response?.data?.detail || 'Could not send the request'
  }
}

async function handleWithdrawRequest(application: Application) {
  requestError.value = null
  try {
    await applicationsApi.withdraw(application.id)
    await loadRequests()
  } catch (e: any) {
    requestError.value = e.response?.data?.detail || 'Could not withdraw it'
  }
}

// Owners: the sysops who see this BBS in the console. Admins add and remove them.
const allUsers = ref<User[]>([])
const newOwnerId = ref<number | ''>('')
const ownerError = ref<string | null>(null)
const ownerCandidates = computed(() => {
  const owned = new Set((clientsStore.currentClient?.owners ?? []).map((o) => o.user_id))
  return allUsers.value.filter((u) => !owned.has(u.id))
})

async function loadUsers() {
  if (!authStore.isAdmin) return
  try {
    allUsers.value = (await usersApi.list()).data
  } catch {
    allUsers.value = []
  }
}

async function handleAddOwner() {
  if (newOwnerId.value === '' || !clientsStore.currentClient) return
  ownerError.value = null
  try {
    clientsStore.currentClient.owners = (
      await clientsApi.addOwner(clientId.value, Number(newOwnerId.value))
    ).data
    newOwnerId.value = ''
  } catch (e: any) {
    ownerError.value = e.response?.data?.detail || 'Could not add the owner'
  }
}

async function handleRemoveOwner(userId: number) {
  if (!clientsStore.currentClient) return
  ownerError.value = null
  try {
    clientsStore.currentClient.owners = (await clientsApi.removeOwner(clientId.value, userId)).data
  } catch (e: any) {
    ownerError.value = e.response?.data?.detail || 'Could not remove the owner'
  }
}

// Each league's polls, queue and the hub game's view; null while loading.
const leagueStatus = ref<LeagueStatusResponse | null>(null)

async function loadLeagueStatus() {
  try {
    leagueStatus.value = (await clientsApi.leagueStatus(clientId.value)).data
  } catch {
    leagueStatus.value = { tracking_since: null, rows: [] }
  }
}

onMounted(async () => {
  await clientsStore.loadClient(clientId.value)
  await Promise.all([loadClaimStatus(), loadUsers(), loadRequests(), loadLeagueStatus()])
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
  const result = await clientsStore.regenerateSecret(clientId.value, overlapHours.value)
  if (result) {
    newSecret.value = result.client_secret
    newSecretOldUntil.value = result.old_secret_until
    showSecretModal.value = true
    secretError.value = ''
    if (clientsStore.currentClient) clientsStore.currentClient.old_secret_until = result.old_secret_until
  } else {
    secretError.value = clientsStore.error || 'Could not regenerate the secret'
  }
}

// The leagues this BBS is still in. The hub refuses to delete it until it has
// been removed from each, so say so before the admin presses Delete.
const memberOf = computed(() =>
  (clientsStore.currentClient?.league_memberships ?? []).map((m) => m.full_id)
)

function openDeleteModal() {
  clientsStore.clearError()
  showDeleteModal.value = true
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
            <button class="btn btn-danger" @click="openDeleteModal">Delete</button>
          </div>
        </header>

        <div v-if="clientsStore.currentClient.claim_waiting" class="alert alert-info collect">
          <span>
            <strong>Your credentials are ready.</strong> Collect them once, with a config file for your
            client already filled in.
          </span>
          <button class="btn btn-primary btn-sm" :disabled="collecting" @click="handleCollect">
            {{ collecting ? 'Opening...' : 'Collect credentials' }}
          </button>
        </div>
        <div v-if="claimError && !authStore.isAdmin" class="alert alert-danger">{{ claimError }}</div>

        <div v-if="clientsStore.currentClient.stage && clientsStore.currentClient.stage !== 'active'" class="card mb-4">
          <div class="card-body">
            <ol class="stages">
              <li
                v-for="(s, i) in STAGES"
                :key="s.key"
                :class="{ done: i < stageIndex, current: i === stageIndex }"
              >{{ s.label }}</li>
            </ol>
            <p class="form-hint mt-2">{{ STAGE_HINT[clientsStore.currentClient.stage] }}</p>
          </div>
        </div>

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

              <div class="sample-config mt-4">
                Sample client config:
                <a :href="clientsApi.sampleConfigUrl(clientId, 'linux')" download>config.toml</a> (Linux)
                &middot;
                <a :href="clientsApi.sampleConfigUrl(clientId, 'windows')" download>config.psd1</a> (Windows)
                <p class="form-hint mt-1">
                  Your hub address, client ID, leagues and BBS numbers are filled in. Before you use it,
                  replace every CHANGE-ME: the game paths, which only you know, and the client secret,
                  which the hub does not keep. Use the secret you saved when you claimed your credentials,
                  or regenerate it below. Then run the validator before the first sync.
                </p>
              </div>

              <div v-if="authStore.isAdmin" class="credential-actions mt-4">
                <select v-if="clientsStore.currentClient.owners?.length" v-model="claimFor" class="claim-for">
                  <option value="">For anyone with the link</option>
                  <option v-for="o in clientsStore.currentClient.owners" :key="o.user_id" :value="o.user_id">
                    Only for {{ o.username }}, signed in
                  </option>
                </select>
                <button class="btn btn-primary btn-sm" @click="handleIssueClaimLink">
                  Issue Claim Link
                </button>
              </div>
              <p v-if="authStore.isAdmin" class="form-hint mt-2">
                <template v-if="claimStatus">Last claim link: {{ claimStatusText(claimStatus) }}.</template>
                <template v-else>
                  Send the sysop a claim link rather than a secret: it works once and expires in 72 hours.
                </template>
              </p>
              <div v-if="claimError" class="alert alert-danger mt-2">{{ claimError }}</div>

              <div class="credential-actions mt-4">
                <select v-model="overlapHours" class="claim-for">
                  <option :value="24">Keep the old secret working for 24 hours</option>
                  <option :value="0">Stop the old secret now</option>
                </select>
                <button class="btn btn-secondary btn-sm" @click="handleRegenerateSecret">
                  Regenerate Secret
                </button>
              </div>
              <p class="form-hint mt-2">
                <template v-if="clientsStore.currentClient.old_secret_until">
                  The old secret works until
                  {{ new Date(clientsStore.currentClient.old_secret_until).toLocaleString() }}; after that,
                  only the new one does.
                </template>
                <template v-else>
                  A new secret goes in the client config. If the old one got out, stop it now: that also
                  signs the BBS out until its config has the new one.
                </template>
              </p>
              <div v-if="secretError" class="alert alert-danger mt-2">{{ secretError }}</div>
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

        <!-- A sysop asking for more leagues -->
        <div v-if="!authStore.isAdmin" class="card mt-4">
          <div class="card-header">
            <h3>More leagues</h3>
          </div>
          <div class="card-body">
            <div v-if="requestError" class="alert alert-error mb-4">{{ requestError }}</div>
            <template v-if="pendingRequest">
              <p>
                Asked for {{ pendingRequest.leagues.map((l) => l.full_id).join(', ') }}
                on {{ new Date(pendingRequest.created_at).toLocaleDateString() }}; waiting for the hub admin.
              </p>
              <button class="btn btn-secondary btn-sm" @click="handleWithdrawRequest(pendingRequest)">Withdraw</button>
            </template>
            <template v-else>
              <p v-if="lastDecided" class="form-hint mb-4">
                Your last request was {{ lastDecided.status }}<template v-if="lastDecided.decision_note">:
                {{ lastDecided.decision_note }}</template>
              </p>
              <p v-if="!requestableLeagues.length" class="text-muted">No other leagues are open to join.</p>
              <form v-else @submit.prevent="handleRequestLeagues">
                <label v-for="l in requestableLeagues" :key="l.id" class="league-choice">
                  <input v-model="requestLeagueIds" type="checkbox" :value="l.id" />
                  <span class="font-mono">{{ l.full_id }}</span> {{ l.name }}
                </label>
                <textarea v-model="requestNote" rows="2" maxlength="2000" placeholder="Anything the admin should know (optional)" class="mt-2"></textarea>
                <button type="submit" class="btn btn-primary btn-sm mt-2">Ask to join</button>
              </form>
              <p class="form-hint mt-2">
                Your credentials stay the same. Once approved, add the league to your client's config with
                the BBS number shown above.
              </p>
            </template>
          </div>
        </div>

        <!-- Owners -->
        <div class="card mt-4">
          <div class="card-header">
            <h3>Sysops</h3>
          </div>
          <div class="card-body">
            <div v-if="ownerError" class="alert alert-error mb-4">{{ ownerError }}</div>
            <p v-if="!clientsStore.currentClient.owners?.length" class="text-muted">
              No one yet. A sysop signs in to create their account; then make them an owner here.
            </p>
            <ul v-else class="owner-list">
              <li v-for="owner in clientsStore.currentClient.owners" :key="owner.user_id">
                <span class="owner-name">{{ owner.username }}</span>
                <span v-if="owner.full_name || owner.email" class="text-muted">
                  {{ [owner.full_name, owner.email].filter(Boolean).join(' · ') }}
                </span>
                <button
                  v-if="authStore.isAdmin"
                  class="btn btn-sm btn-secondary"
                  @click="handleRemoveOwner(owner.user_id)"
                >
                  Remove
                </button>
              </li>
            </ul>
            <form v-if="authStore.isAdmin" class="address-form mt-2" @submit.prevent="handleAddOwner">
              <select v-model="newOwnerId">
                <option value="" disabled>Choose a user...</option>
                <option v-for="u in ownerCandidates" :key="u.id" :value="u.id">
                  {{ u.username }}{{ u.email ? ` (${u.email})` : '' }}
                </option>
              </select>
              <button type="submit" class="btn btn-sm btn-primary" :disabled="newOwnerId === ''">
                Add Owner
              </button>
            </form>
            <p v-if="authStore.isAdmin" class="form-hint mt-2">
              Owners see this BBS, its leagues, its packets and its runs. They can't change anything
              the admin manages.
            </p>
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

        <!-- League Status -->
        <div class="card mt-4">
          <div class="card-header">
            <h3>League Status</h3>
          </div>
          <div class="card-body" style="padding: 0;">
            <LeagueStatusTable
              by="client"
              :rows="leagueStatus?.rows ?? []"
              :tracking-since="leagueStatus?.tracking_since ?? null"
              :loading="leagueStatus === null"
            />
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
            <div v-if="clientsStore.error" class="alert alert-error mb-4">
              {{ clientsStore.error }}
            </div>
            <template v-if="memberOf.length">
              <p>
                <strong>{{ clientsStore.currentClient?.bbs_name }}</strong> is still a member of
                {{ memberOf.join(', ') }}. Remove it from those leagues first.
              </p>
            </template>
            <template v-else>
              <p>Are you sure you want to delete <strong>{{ clientsStore.currentClient?.bbs_name }}</strong>?</p>
              <p class="text-muted mt-2">This action cannot be undone.</p>
            </template>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-secondary" @click="showDeleteModal = false">
              Cancel
            </button>
            <button
              type="button"
              class="btn btn-danger"
              @click="handleDelete"
              :disabled="clientsStore.loading || memberOf.length > 0"
            >
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
            <p v-if="newSecretOldUntil" class="form-hint mb-4">
              The old secret keeps working until {{ new Date(newSecretOldUntil).toLocaleString() }}.
              Put this one in the client config before then.
            </p>
            <p v-else class="form-hint mb-4">
              The old secret has stopped working, and the BBS is signed out until its client config has
              this one.
            </p>
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

.collect {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}

.stages {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  list-style: none;
  padding: 0;
  margin: 0;
  counter-reset: stage;
}

.stages li {
  counter-increment: stage;
  padding: 0.25rem 0.75rem;
  border-radius: 999px;
  font-size: 0.8125rem;
  background: var(--color-background, #f1f5f9);
  color: var(--color-text-muted, #64748b);
}

.stages li::before {
  content: counter(stage) '. ';
}

.stages li.done {
  background: #dcfce7;
  color: #166534;
}

.stages li.current {
  background: var(--color-primary);
  color: #fff;
}

.claim-for {
  font-size: 0.8125rem;
}

.league-choice {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.375rem;
}

.owner-list {
  list-style: none;
  padding: 0;
  margin: 0 0 0.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.owner-list li {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.owner-name {
  font-weight: 500;
}
</style>
