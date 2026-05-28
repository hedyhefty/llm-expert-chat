<script setup lang="ts">
import { ref } from 'vue'
import type { ChatExpertEvent } from '../api/client'
import { streamChat } from '../api/client'
import { renderMarkdown } from '../utils/markdown'

type ChatMode = 'normal' | 'expert'
type ExpertDetail = {
  role: string
  title: string
  providerName: string
  model: string
  content: string
  reasoning: string
  done: boolean
  error?: string
}
type Message = {
  id: string
  role: 'user' | 'assistant'
  content: string
  reasoning?: string
  experts?: ExpertDetail[]
  status?: string
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
  const assistantIndex =
    messages.value.push({
      id: crypto.randomUUID(),
      role: 'assistant',
      content: '',
      status: mode.value === 'expert' ? 'Starting expert team' : 'Contacting model',
    }) - 1
  const assistantMessage = messages.value[assistantIndex]
  input.value = ''
  loading.value = true

  try {
    await streamChat(
      content,
      mode.value,
      (token) => {
        assistantMessage.status = 'Writing answer'
        assistantMessage.content += token
      },
      (token) => {
        if (!assistantMessage.content.trim()) {
          assistantMessage.status = 'Thinking'
        }
        assistantMessage.reasoning = `${assistantMessage.reasoning ?? ''}${token}`
      },
      (event) => {
        applyExpertEvent(assistantMessage, event)
      },
    )
  } catch (error) {
    assistantMessage.content = error instanceof Error ? error.message : 'Request failed'
  } finally {
    assistantMessage.status = undefined
    loading.value = false
  }
}

function applyExpertEvent(message: Message, event: ChatExpertEvent) {
  if (!message.experts) {
    message.experts = []
  }

  let detail = message.experts.find((expert) => expert.role === event.role)
  if (!detail) {
    detail = {
      role: event.role,
      title: event.title,
      providerName: event.provider_name,
      model: event.model,
      content: '',
      reasoning: '',
      done: false,
    }
    message.experts.push(detail)
  }

  detail.title = event.title
  detail.providerName = event.provider_name
  detail.model = event.model

  if (event.event === 'expert_delta' && event.content) {
    message.status = `${event.title} is drafting`
    detail.content += event.content
  } else if (event.event === 'expert_reasoning_delta' && event.reasoning) {
    message.status = `${event.title} is thinking`
    detail.reasoning += event.reasoning
  } else if (event.event === 'expert_start') {
    message.status = `${event.title} is working`
  } else if (event.event === 'expert_done') {
    detail.done = true
    message.status = event.role === 'synthesizer' ? 'Finalizing answer' : `${event.title} finished`
    detail.error = event.error ?? undefined
    if (event.content !== undefined) {
      detail.content = event.content
    }
    if (event.reasoning !== undefined) {
      detail.reasoning = event.reasoning
    }
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

function pendingLabel(message: Message): string {
  if (message.content.trim() || message.reasoning?.trim() || message.experts?.length) {
    return ''
  }
  return message.status ?? 'Organizing answer'
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
          <p v-if="pendingLabel(message)" class="message-pending">
            {{ pendingLabel(message) }}<span class="typing-dots" aria-hidden="true">...</span>
          </p>
          <p v-if="message.status && !pendingLabel(message)" class="message-status">
            {{ message.status }}<span class="typing-dots" aria-hidden="true">...</span>
          </p>
          <div
            v-if="assistantAnswer(message)"
            class="markdown-body"
            v-html="renderMarkdown(assistantAnswer(message))"
          />
          <details v-if="assistantReasoning(message)" class="reasoning-panel">
            <summary>Reasoning</summary>
            <div class="markdown-body reasoning-markdown" v-html="renderMarkdown(assistantReasoning(message))" />
          </details>
          <details v-if="message.experts?.length" class="experts-panel">
            <summary>Expert details</summary>
            <article v-for="expert in message.experts" :key="expert.role" class="expert-detail">
              <header>
                <div>
                  <h3>{{ expert.title }}</h3>
                  <span>{{ expert.providerName }} / {{ expert.model }}</span>
                </div>
                <span class="expert-status">{{ expert.done ? 'Done' : 'Running' }}</span>
              </header>
              <div
                v-if="expert.content"
                class="markdown-body expert-markdown"
                v-html="renderMarkdown(expert.content)"
              />
              <details v-if="expert.reasoning" class="expert-reasoning">
                <summary>Reasoning</summary>
                <div class="markdown-body reasoning-markdown" v-html="renderMarkdown(expert.reasoning)" />
              </details>
              <p v-if="expert.error" class="expert-error">{{ expert.error }}</p>
            </article>
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
