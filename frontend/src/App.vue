<script setup lang="ts">
import { onMounted, ref } from 'vue'
import type { AuthResponse, User } from './api/client'
import { clearAuthToken, getAuthToken, getMe, setAuthToken } from './api/client'
import AuthView from './views/AuthView.vue'
import ChatView from './views/ChatView.vue'
import SettingsView from './views/SettingsView.vue'

type ActiveView = 'chat' | 'settings'

const activeView = ref<ActiveView>('chat')
const booting = ref(true)
const user = ref<User | null>(null)

onMounted(async () => {
  if (!getAuthToken()) {
    booting.value = false
    return
  }

  try {
    user.value = await getMe()
  } catch {
    clearAuthToken()
  } finally {
    booting.value = false
  }
})

function handleAuthenticated(auth: AuthResponse) {
  setAuthToken(auth.access_token)
  user.value = auth.user
}

function signOut() {
  clearAuthToken()
  user.value = null
  activeView.value = 'chat'
}
</script>

<template>
  <main v-if="booting" class="boot-shell">Loading</main>

  <AuthView v-else-if="!user" @authenticated="handleAuthenticated" />

  <main v-else class="app-shell">
    <aside class="sidebar">
      <button class="new-chat">New chat</button>
      <nav class="nav-list">
        <button :class="{ active: activeView === 'chat' }" @click="activeView = 'chat'">Chat</button>
        <button :class="{ active: activeView === 'settings' }" @click="activeView = 'settings'">Settings</button>
      </nav>
      <div class="account-block">
        <span>{{ user.email }}</span>
        <button type="button" @click="signOut">Sign out</button>
      </div>
    </aside>

    <ChatView v-if="activeView === 'chat'" />
    <SettingsView v-else />
  </main>
</template>
