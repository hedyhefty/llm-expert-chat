<script setup lang="ts">
import { reactive, ref } from 'vue'
import type { AuthResponse } from '../api/client'
import { login, register } from '../api/client'

type AuthMode = 'login' | 'register'

const emit = defineEmits<{
  authenticated: [auth: AuthResponse]
}>()

const mode = ref<AuthMode>('login')
const loading = ref(false)
const error = ref('')
const form = reactive({
  email: '',
  password: '',
})

async function submitAuth() {
  error.value = ''
  loading.value = true

  try {
    const auth =
      mode.value === 'login'
        ? await login(form.email.trim(), form.password)
        : await register(form.email.trim(), form.password)
    emit('authenticated', auth)
  } catch (authError) {
    error.value = authError instanceof Error ? authError.message : 'Authentication failed'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="auth-shell">
    <section class="auth-panel">
      <div>
        <h1>LLM Expert Chat</h1>
        <p>{{ mode === 'login' ? 'Sign in to continue' : 'Create your local account' }}</p>
      </div>

      <form class="auth-form" @submit.prevent="submitAuth">
        <label>
          Email
          <input v-model="form.email" type="email" autocomplete="email" required />
        </label>
        <label>
          Password
          <input
            v-model="form.password"
            type="password"
            autocomplete="current-password"
            minlength="8"
            required
          />
        </label>

        <p v-if="error" class="form-error">{{ error }}</p>

        <button type="submit" :disabled="loading">
          {{ loading ? 'Working' : mode === 'login' ? 'Sign in' : 'Create account' }}
        </button>
      </form>

      <button class="text-button" type="button" @click="mode = mode === 'login' ? 'register' : 'login'">
        {{ mode === 'login' ? 'Create an account' : 'Use existing account' }}
      </button>
    </section>
  </main>
</template>
