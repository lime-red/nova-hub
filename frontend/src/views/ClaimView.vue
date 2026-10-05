<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { claimApi } from '@/services/api'
import { GAMES } from '@/games'

// A sysop collecting their BBS's credentials. Public: the link is the
// credential. Opening it only looks; pressing Claim generates the secret,
// which this page shows once and nothing ever shows again.

interface ClaimStatus {
  bbs_name: string
  state: 'ready' | 'used' | 'superseded' | 'expired'
  expires_at: string
  used_at?: string | null
  used_ip?: string | null
}

interface ClaimResult {
  bbs_name: string
  hub_url: string
  client_id: string
  client_secret: string
  leagues: { game: string; number: string; bbs_index: number }[]
  config_toml: string
  config_psd1: string
}

const CLIENT_REPO = 'https://github.com/lime-red/nova-client'

const route = useRoute()
const token = computed(() => String(route.params.token))

const loading = ref(true)
const notFound = ref(false)
const error = ref<string | null>(null)
const status = ref<ClaimStatus | null>(null)
const result = ref<ClaimResult | null>(null)
const claiming = ref(false)
const platform = ref<'linux' | 'windows'>(
  navigator.userAgent.includes('Windows') ? 'windows' : 'linux'
)
const copied = ref<string | null>(null)

onMounted(async () => {
  try {
    status.value = (await claimApi.status(token.value)).data
  } catch (e: any) {
    if (e.response?.status === 404 || e.response?.status === 422) {
      notFound.value = true
    } else {
      error.value = e.response?.data?.detail || 'The hub could not be reached.'
    }
  } finally {
    loading.value = false
  }
})

async function handleClaim() {
  if (claiming.value) return
  claiming.value = true
  error.value = null
  try {
    result.value = (await claimApi.claim(token.value)).data
  } catch (e: any) {
    error.value = e.response?.data?.detail || 'The claim failed.'
    // Someone may have got there first; show what the link says now.
    try {
      status.value = (await claimApi.status(token.value)).data
    } catch {
      /* keep the error above */
    }
  } finally {
    claiming.value = false
  }
}

// "015" + "BRE" -> "015B", as league ids are written everywhere else
function letterFor(code: string): string {
  return GAMES.find((g) => g.code === code)?.letter ?? code
}

function when(iso?: string | null): string {
  return iso ? new Date(iso).toLocaleString() : ''
}

async function copy(what: string, text: string) {
  await navigator.clipboard.writeText(text)
  copied.value = what
  setTimeout(() => (copied.value = null), 1500)
}

const configName = computed(() => (platform.value === 'windows' ? 'config.psd1' : 'config.toml'))

function downloadConfig() {
  if (!result.value) return
  const text = platform.value === 'windows' ? result.value.config_psd1 : result.value.config_toml
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }))
  const a = document.createElement('a')
  a.href = url
  a.download = configName.value
  a.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <div class="claim-page">
    <div class="claim-card">
      <h1>Nova Hub</h1>

      <p v-if="loading" class="text-muted">Checking your link...</p>

      <template v-else-if="notFound">
        <h2>This link is not valid</h2>
        <p>Check you copied all of it. If it still does not work, ask the hub admin for a new one.</p>
      </template>

      <template v-else-if="result">
        <h2>Credentials for {{ result.bbs_name }}</h2>
        <div class="alert alert-warning">
          This is the only time these are shown. Save them, or download the config file, before you
          leave this page. If you lose them, ask the hub admin for a new link.
        </div>

        <dl class="credentials">
          <dt>Hub</dt>
          <dd class="font-mono">{{ result.hub_url }}</dd>
          <dt>Client ID</dt>
          <dd>
            <span class="font-mono">{{ result.client_id }}</span>
            <button class="btn btn-secondary btn-sm" @click="copy('id', result.client_id)">
              {{ copied === 'id' ? 'Copied' : 'Copy' }}
            </button>
          </dd>
          <dt>Client secret</dt>
          <dd>
            <span class="font-mono secret">{{ result.client_secret }}</span>
            <button class="btn btn-secondary btn-sm" @click="copy('secret', result.client_secret)">
              {{ copied === 'secret' ? 'Copied' : 'Copy' }}
            </button>
          </dd>
          <dt>Leagues</dt>
          <dd>
            <span v-if="!result.leagues.length" class="text-muted">None yet</span>
            <span v-for="l in result.leagues" :key="l.game + l.number" class="league font-mono">
              {{ l.number }}{{ letterFor(l.game) }} as BBS #{{ l.bbs_index }}
            </span>
          </dd>
        </dl>

        <h3>Set up your client</h3>
        <div class="platform-switch" role="tablist">
          <button
            :class="['btn', 'btn-sm', platform === 'windows' ? 'btn-primary' : 'btn-secondary']"
            @click="platform = 'windows'"
          >Windows</button>
          <button
            :class="['btn', 'btn-sm', platform === 'linux' ? 'btn-primary' : 'btn-secondary']"
            @click="platform = 'linux'"
          >Linux</button>
        </div>

        <ol class="steps">
          <li>
            Download Nova Client from <a :href="CLIENT_REPO" target="_blank" rel="noopener">{{ CLIENT_REPO }}</a>
            <template v-if="platform === 'windows'"> and use the <code>powershell</code> folder.</template>
            <template v-else> and use the <code>python</code> folder.</template>
          </li>
          <li>
            <button class="btn btn-primary btn-sm" @click="downloadConfig">Download {{ configName }}</button>
            and put it in that folder. Your hub address, credentials, leagues and BBS numbers are already
            filled in.
          </li>
          <li>
            Edit every path marked <code>CHANGE-ME</code> to where each game is installed on your BBS.
            <template v-if="platform === 'linux'">
              Under dosemu, <code>game_folder</code> is the folder as Linux sees it and
              <code>game_dos_path</code> is the same folder as DOS sees it.
            </template>
          </li>
          <li>
            Check it, do one sync, then run it continuously:
            <pre v-if="platform === 'windows'">.\NovaClient-WinPS5.ps1 -Validate
.\NovaClient-WinPS5.ps1 -Once -Verbose
.\NovaClient-WinPS5.ps1 -Daemon</pre>
            <pre v-else>python client.py --validate
python client.py --verbose
python daemon.py --verbose</pre>
          </li>
        </ol>
      </template>

      <template v-else-if="status">
        <h2>Credentials for {{ status.bbs_name }}</h2>

        <template v-if="status.state === 'ready'">
          <p>
            This link gives your BBS the client ID and secret it uses to exchange packets with the hub.
            It works once, until {{ when(status.expires_at) }}.
          </p>
          <p class="text-muted">
            Claiming it creates a new secret. If your BBS is already connected to this hub, it stops
            syncing until you give it the new one.
          </p>
          <div v-if="error" class="alert alert-danger">{{ error }}</div>
          <button class="btn btn-primary" :disabled="claiming" @click="handleClaim">
            {{ claiming ? 'Claiming...' : 'Claim credentials' }}
          </button>
        </template>

        <template v-else-if="status.state === 'used'">
          <div class="alert alert-danger">
            This link was used on {{ when(status.used_at) }} from {{ status.used_ip }}.
          </div>
          <p>
            If that was not you, tell the hub admin now: someone else has your BBS's credentials, and
            the admin will issue you a new link, which replaces them.
          </p>
        </template>

        <template v-else-if="status.state === 'superseded'">
          <p>A newer link has been issued for this BBS, so this one no longer works. Use the latest one.</p>
        </template>

        <template v-else>
          <p>This link expired on {{ when(status.expires_at) }}. Ask the hub admin for a new one.</p>
        </template>
      </template>

      <div v-else-if="error" class="alert alert-danger">{{ error }}</div>
    </div>
  </div>
</template>

<style scoped>
.claim-page {
  min-height: 100vh;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  background: linear-gradient(135deg, #1e293b 0%, #334155 100%);
  padding: 3rem 1rem;
}

.claim-card {
  width: 100%;
  max-width: 640px;
  background: var(--color-surface);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  padding: 2rem;
}

.claim-card h1 {
  font-size: 1.25rem;
  color: var(--color-primary);
  margin-bottom: 1rem;
}

.claim-card h2 {
  font-size: 1.5rem;
  margin-bottom: 1rem;
}

.claim-card h3 {
  margin: 1.5rem 0 0.75rem;
}

.claim-card p {
  margin-bottom: 1rem;
}

.credentials {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 0.5rem 1rem;
  margin: 1rem 0;
}

.credentials dt {
  font-weight: 600;
}

.credentials dd {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  margin: 0;
  min-width: 0;
}

.secret {
  word-break: break-all;
}

.league {
  margin-right: 0.75rem;
}

.platform-switch {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1rem;
}

.steps li {
  margin-bottom: 0.75rem;
}

.steps pre {
  margin-top: 0.5rem;
  padding: 0.75rem;
  background: var(--color-background, #f1f5f9);
  border-radius: var(--radius-md, 6px);
  overflow-x: auto;
}
</style>
