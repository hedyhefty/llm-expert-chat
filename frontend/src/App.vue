<script setup lang="ts">
import { onMounted, ref } from 'vue'
import type { AuthResponse, ConversationSummary, User } from './api/client'
import { clearAuthToken, getAuthToken, getMe, listConversations, setAuthToken } from './api/client'
import AuthView from './views/AuthView.vue'
import ChatView from './views/ChatView.vue'
import SettingsView from './views/SettingsView.vue'

type ActiveView = 'chat' | 'settings'

const activeView = ref<ActiveView>('chat')
const booting = ref(true)
const user = ref<User | null>(null)
const conversations = ref<ConversationSummary[]>([])
const activeConversationId = ref<string | null>(null)

onMounted(async () => {
  if (!getAuthToken()) {
    booting.value = false
    return
  }

  try {
    user.value = await getMe()
    await loadConversations()
  } catch {
    clearAuthToken()
  } finally {
    booting.value = false
  }
})

function handleAuthenticated(auth: AuthResponse) {
  setAuthToken(auth.access_token)
  user.value = auth.user
  void loadConversations()
}

function signOut() {
  clearAuthToken()
  user.value = null
  conversations.value = []
  activeConversationId.value = null
  activeView.value = 'chat'
}

async function loadConversations() {
  conversations.value = await listConversations()
}

function startNewChat() {
  activeView.value = 'chat'
  activeConversationId.value = null
}

function selectConversation(conversationId: string) {
  activeView.value = 'chat'
  activeConversationId.value = conversationId
}

async function handleConversationCreated(conversation: ConversationSummary) {
  activeConversationId.value = conversation.id
  await loadConversations()
}

async function handleConversationUpdated() {
  await loadConversations()
}
</script>

<template>
  <main v-if="booting" class="boot-shell">Loading</main>

  <AuthView v-else-if="!user" @authenticated="handleAuthenticated" />

  <main v-else class="app-shell">
    <aside class="sidebar">
      <button class="new-chat" @click="startNewChat">New chat</button>
      <nav class="nav-list">
        <button :class="{ active: activeView === 'chat' }" @click="activeView = 'chat'">Chat</button>
        <button :class="{ active: activeView === 'settings' }" @click="activeView = 'settings'">Settings</button>
      </nav>
      <div class="conversation-list">
        <button
          v-for="conversation in conversations"
          :key="conversation.id"
          :class="{ active: activeConversationId === conversation.id && activeView === 'chat' }"
          @click="selectConversation(conversation.id)"
        >
          {{ conversation.title }}
        </button>
      </div>
      <div class="account-block">
        <span>{{ user.email }}</span>
        <button type="button" @click="signOut">Sign out</button>
      </div>
    </aside>

    <ChatView
      v-if="activeView === 'chat'"
      :conversation-id="activeConversationId"
      @conversation-created="handleConversationCreated"
      @conversation-updated="handleConversationUpdated"
    />
    <SettingsView v-else />
  </main>
</template>
