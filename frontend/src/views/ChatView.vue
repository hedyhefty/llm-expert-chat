<script setup lang="ts">
import { ref } from 'vue'
import { streamChat } from '../api/client'

type ChatMode = 'normal' | 'expert'
type Message = {
  id: string
  role: 'user' | 'assistant'
  content: string
  reasoning?: string
}
type AssistantParts = {
  answer: string
  reasoning: string
}

const mode = ref<ChatMode>('normal')
const input = ref('')
const loading = ref(false)
const messages = ref<Message[]>([
  {
    id: 'welcome',
    role: 'assistant',
    content: 'Ready.',
  },
])

async function sendMessage() {
  const content = input.value.trim()
  if (!content || loading.value) {
    return
  }

  messages.value.push({ id: crypto.randomUUID(), role: 'user', content })
  const assistantMessage: Message = { id: crypto.randomUUID(), role: 'assistant', content: '' }
  messages.value.push(assistantMessage)
  input.value = ''
  loading.value = true

  try {
    await streamChat(
      content,
      mode.value,
      (token) => {
        assistantMessage.content += token
      },
      (token) => {
        assistantMessage.reasoning = `${assistantMessage.reasoning ?? ''}${token}`
      },
    )
  } catch (error) {
    assistantMessage.content = error instanceof Error ? error.message : 'Request failed'
  } finally {
    loading.value = false
  }
}

function assistantParts(content: string): AssistantParts {
  const reasoning: string[] = []
  const answer = content.replace(/<think>([\s\S]*?)(?:<\/think>|$)/gi, (_match, thought) => {
    const cleaned = String(thought).trim()
    if (cleaned) {
      reasoning.push(cleaned)
    }
    return ''
  })

  return {
    answer: answer.trim(),
    reasoning: reasoning.join('\n\n').trim(),
  }
}

function assistantAnswer(message: Message): string {
  if (message.reasoning) {
    return message.content.trim()
  }
  return assistantParts(message.content).answer
}

function assistantReasoning(message: Message): string {
  if (message.reasoning) {
    return message.reasoning.trim()
  }
  return assistantParts(message.content).reasoning
}
</script>

<template>
  <section class="chat-view">
    <header class="topbar">
      <div>
        <h1>LLM Expert Chat</h1>
        <p>{{ mode === 'normal' ? 'Normal' : 'Expert' }}</p>
      </div>

      <div class="mode-toggle" aria-label="Chat mode">
        <button :class="{ selected: mode === 'normal' }" @click="mode = 'normal'">Normal</button>
        <button :class="{ selected: mode === 'expert' }" @click="mode = 'expert'">Expert</button>
      </div>
    </header>

    <div class="message-list">
      <article v-for="message in messages" :key="message.id" :class="['message', message.role]">
        <template v-if="message.role === 'assistant'">
          <p v-if="assistantAnswer(message)">{{ assistantAnswer(message) }}</p>
          <details v-if="assistantReasoning(message)" class="reasoning-panel">
            <summary>Reasoning</summary>
            <p>{{ assistantReasoning(message) }}</p>
          </details>
        </template>
        <p v-else>{{ message.content }}</p>
      </article>
    </div>

    <form class="composer" @submit.prevent="sendMessage">
      <textarea v-model="input" rows="3" placeholder="Message" />
      <button type="submit" :disabled="loading || !input.trim()">
        {{ loading ? 'Sending' : 'Send' }}
      </button>
    </form>
  </section>
</template>
