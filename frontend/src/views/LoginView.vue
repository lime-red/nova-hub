<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useRouter, useRoute } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import { authApi } from "@/services/api";

const isHttp = computed(() => window.location.protocol === "http:");

// Baked in by vite from package.json. The login page cannot ask the API for a
// version -- it is the page you see precisely because you are not authenticated.
const appVersion = __APP_VERSION__;

const router = useRouter();
const route = useRoute();
const authStore = useAuthStore();

const username = ref("");
const password = ref("");
const isSubmitting = ref(false);

// Sysops sign in through the identity provider; the password form is the hub
// admin's break-glass. Until /auth/methods answers, assume password only.
const ssoEnabled = ref(false);
const showPassword = ref(false);

onMounted(async () => {
    try {
        ssoEnabled.value = (await authApi.methods()).data.sso;
    } catch {
        ssoEnabled.value = false;
    }
});

const redirectTo = computed(() => (route.query.redirect as string) || "/dashboard");
const ssoUrl = computed(() => authApi.ssoStartUrl(redirectTo.value));

// /auth/sso/callback sends the browser back here with ?sso_error=<code>.
const SSO_ERRORS: Record<string, string> = {
    cancelled: "Sign-in was cancelled.",
    state: "That sign-in expired or was started in another browser. Please try again.",
    provider: "The sign-in provider did not accept the sign-in. Please try again.",
    disabled: "Sign-in through a provider is not enabled on this hub.",
    unverified_email: "Verify your email address with the sign-in provider first, then try again.",
    email_in_use:
        "A hub account already uses this email address. Ask the hub admin for a link to connect your sign-in to it.",
    inactive: "This hub account has been disabled. Ask the hub admin.",
    identity_in_use:
        "That sign-in already belongs to a different hub account. Ask the hub admin to sort it out.",
    relink_used: "That link has already been used, replaced or has expired. Ask the hub admin for a new one.",
    relink_invalid: "That link is not valid. Check you copied all of it.",
};
const ssoError = computed(() => {
    const code = route.query.sso_error as string | undefined;
    return code ? SSO_ERRORS[code] || "Sign-in failed. Please try again." : null;
});

async function handleLogin() {
    if (isSubmitting.value) return;

    isSubmitting.value = true;
    authStore.clearError();

    const success = await authStore.login(username.value, password.value);

    if (success) {
        router.push(redirectTo.value);
    }

    isSubmitting.value = false;
}
</script>

<template>
    <div class="login-page">
        <div class="login-container">
            <div class="login-card">
                <div class="login-header">
                    <h1>Nova Hub</h1>
                    <p class="text-muted">BBS Inter-League Routing System</p>
                </div>

                <div v-if="ssoError" class="alert alert-error">{{ ssoError }}</div>

                <div v-if="ssoEnabled" class="sso">
                    <a :href="ssoUrl" class="btn btn-primary btn-lg w-full">Sign in</a>
                    <p class="text-muted sso-hint">
                        With an email code, a password, Discord or GitHub. New here? Signing in
                        creates your account.
                    </p>
                    <button
                        type="button"
                        class="btn-link"
                        @click="showPassword = !showPassword"
                    >
                        {{ showPassword ? "Hide" : "Hub admin: sign in with a password" }}
                    </button>
                </div>

                <form
                    v-if="!ssoEnabled || showPassword"
                    @submit.prevent="handleLogin"
                    class="login-form"
                >
                    <div v-if="isHttp" class="alert alert-warning">
                        <strong>Insecure connection:</strong> Session cookies require HTTPS.
                        Login will appear to succeed but all pages will return 401 Unauthorized.
                        Place a TLS-terminating reverse proxy (nginx, Caddy, Tailscale) in front
                        of Nova Hub, or set <code>cookie_secure = false</code> in
                        <code>[security]</code> of <code>config.toml</code> for local use only.
                    </div>

                    <div v-if="authStore.error" class="alert alert-error">
                        {{ authStore.error }}
                    </div>

                    <div class="form-group">
                        <label for="username">Username</label>
                        <input
                            id="username"
                            v-model="username"
                            type="text"
                            placeholder="Enter your username"
                            required
                            autocomplete="username"
                            :disabled="isSubmitting"
                        />
                    </div>

                    <div class="form-group">
                        <label for="password">Password</label>
                        <input
                            id="password"
                            v-model="password"
                            type="password"
                            placeholder="Enter your password"
                            required
                            autocomplete="current-password"
                            :disabled="isSubmitting"
                        />
                    </div>

                    <button
                        type="submit"
                        class="btn btn-primary btn-lg w-full"
                        :disabled="isSubmitting || !username || !password"
                    >
                        <span v-if="isSubmitting" class="spinner"></span>
                        <span v-else>Sign In</span>
                    </button>
                </form>
            </div>

            <p class="login-footer text-muted">Nova Hub v{{ appVersion }}</p>
        </div>
    </div>
</template>

<style scoped>
.login-page {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: linear-gradient(135deg, #1e293b 0%, #334155 100%);
    padding: 1rem;
}

.login-container {
    width: 100%;
    max-width: 400px;
}

.login-card {
    background: var(--color-surface);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-lg);
    padding: 2rem;
}

.login-header {
    text-align: center;
    margin-bottom: 2rem;
}

.login-header h1 {
    font-size: 1.75rem;
    color: var(--color-primary);
    margin-bottom: 0.5rem;
}

.login-form {
    display: flex;
    flex-direction: column;
    gap: 1.25rem;
}

.form-group {
    display: flex;
    flex-direction: column;
    gap: 0.375rem;
}

.form-group label {
    font-weight: 500;
    font-size: 0.875rem;
}

.sso {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 1.25rem;
}

.sso-hint {
    font-size: 0.875rem;
    text-align: center;
}

.btn-link {
    background: none;
    border: none;
    color: var(--color-primary);
    cursor: pointer;
    font-size: 0.875rem;
    padding: 0;
}

.login-footer {
    text-align: center;
    margin-top: 1.5rem;
    font-size: 0.75rem;
}

.spinner {
    width: 1.25rem;
    height: 1.25rem;
    border-color: rgba(255, 255, 255, 0.3);
    border-top-color: white;
}
</style>
