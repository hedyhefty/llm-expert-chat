<script setup lang="ts">
import { ref } from 'vue'
import type { ChatExpertEvent } from '../api/client'
import { streamChat } from '../api/client'
import { renderMarkdown } from '../utils/markdown'

type ChatMode = 'normal' | 'expert' | 'debate'
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
type ActivityStatus = 'pending' | 'running' | 'done'
type TeamActivity = {
  key: string
  label: string
  status: ActivityStatus
}
type Message = {
  id: string
  role: 'user' | 'assistant'
  content: string
  mode?: ChatMode
  reasoning?: string
  experts?: ExpertDetail[]
  activities?: TeamActivity[]
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
      mode: mode.value,
      activities: mode.value === 'debate' ? createDebateActivities() : undefined,
      status: initialStatus(mode.value),
    }) - 1
  const assistantMessage = messages.value[assistantIndex]
  input.value = ''
  loading.value = true

  try {
    await streamChat(
      content,
      mode.value,
      (token) => {
        assistantMessage.status =
          assistantMessage.mode === 'debate' ? 'Synthesizer is writing final answer' : 'Writing answer'
        assistantMessage.content += token
      },
      (token) => {
        if (!assistantMessage.content.trim()) {
          assistantMessage.status =
            assistantMessage.mode === 'debate' ? 'Synthesizer is preparing final answer' : 'Thinking'
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
    detail.content += event.content
  } else if (event.event === 'expert_reasoning_delta' && event.reasoning) {
    detail.reasoning += event.reasoning
  } else if (event.event === 'expert_done') {
    detail.done = true
    detail.error = event.error ?? undefined
    if (event.content !== undefined) {
      detail.content = event.content
    }
    if (event.reasoning !== undefined) {
      detail.reasoning = event.reasoning
    }
  }

  if (message.mode === 'debate') {
    updateDebateActivities(message, event)
    message.status = debateStatus(event)
    return
  }

  if (event.event === 'expert_delta' && event.content) {
    message.status = activeExpertStatus(event)
  } else if (event.event === 'expert_reasoning_delta' && event.reasoning) {
    message.status = reasoningExpertStatus(event)
  } else if (event.event === 'expert_start') {
    message.status = startExpertStatus(event)
  } else if (event.event === 'expert_done') {
    message.status = doneExpertStatus(event)
  }
}

function startExpertStatus(event: ChatExpertEvent): string {
  if (isDebateResponse(event.role)) {
    return 'Debaters are comparing answers'
  }
  if (isDebater(event.role)) {
    return 'Debaters are answering independently'
  }
  if (event.role === 'planner') {
    return 'Planning team strategy'
  }
  if (event.role === 'reviewer') {
    return 'Reviewing expert outputs'
  }
  if (event.role === 'synthesizer') {
    return 'Preparing final answer'
  }
  return `${event.title} is working`
}

function activeExpertStatus(event: ChatExpertEvent): string {
  if (isDebateResponse(event.role)) {
    return 'Debaters are comparing answers'
  }
  if (isDebater(event.role)) {
    return 'Debaters are answering independently'
  }
  if (event.role === 'planner') {
    return 'Building team plan'
  }
  if (event.role === 'reviewer') {
    return 'Auditing expert outputs'
  }
  if (event.role === 'synthesizer') {
    return 'Writing final answer'
  }
  return `${event.title} is drafting`
}

function doneExpertStatus(event: ChatExpertEvent): string {
  if (isDebateResponse(event.role)) {
    return 'Debaters are comparing answers'
  }
  if (isDebater(event.role)) {
    return 'Debaters are answering independently'
  }
  if (event.role === 'planner') {
    return 'Team plan ready'
  }
  if (event.role === 'reviewer') {
    return 'Review complete'
  }
  if (event.role === 'synthesizer') {
    return 'Finalizing answer'
  }
  return `${event.title} finished`
}

function reasoningExpertStatus(event: ChatExpertEvent): string {
  if (isDebateResponse(event.role)) {
    return 'Debaters are comparing answers'
  }
  if (isDebater(event.role)) {
    return 'Debaters are thinking independently'
  }
  return `${event.title} is thinking`
}

function isDebater(role: string): boolean {
  return role.startsWith('debater_')
}

function isDebateResponse(role: string): boolean {
  return isDebater(role) && role.endsWith('_response')
}

function createDebateActivities(): TeamActivity[] {
  return [
    {
      key: 'debater_thinking',
      label: 'Debaters thinking independently',
      status: 'pending',
    },
    {
      key: 'debater_answering',
      label: 'Debaters answering independently',
      status: 'pending',
    },
    {
      key: 'debater_comparing',
      label: 'Debaters comparing answers',
      status: 'pending',
    },
    {
      key: 'synthesis',
      label: 'Synthesizer writing final answer',
      status: 'pending',
    },
  ]
}

function updateDebateActivities(message: Message, event: ChatExpertEvent) {
  if (!message.activities) {
    message.activities = createDebateActivities()
  }

  if (isDebateResponse(event.role)) {
    finishStartedActivity(message, 'debater_thinking')
    setActivityStatus(message, 'debater_answering', 'done')
    setActivityStatus(message, 'debater_comparing', 'running')
    return
  }

  if (isDebater(event.role)) {
    if (event.event === 'expert_reasoning_delta') {
      setActivityStatus(message, 'debater_thinking', 'running')
    } else if (event.event === 'expert_delta' && event.content) {
      setActivityStatus(message, 'debater_answering', 'running')
    }
    return
  }

  if (event.role === 'synthesizer') {
    finishStartedActivity(message, 'debater_thinking')
    setActivityStatus(message, 'debater_answering', 'done')
    setActivityStatus(message, 'debater_comparing', 'done')
    setActivityStatus(message, 'synthesis', event.event === 'expert_done' ? 'done' : 'running')
  }
}

function setActivityStatus(message: Message, key: string, status: ActivityStatus) {
  const activity = message.activities?.find((item) => item.key === key)
  if (!activity || (activity.status === 'done' && status !== 'done')) {
    return
  }
  activity.status = status
}

function finishStartedActivity(message: Message, key: string) {
  const activity = message.activities?.find((item) => item.key === key)
  if (!activity || activity.status === 'pending') {
    return
  }
  activity.status = 'done'
}

function visibleActivities(message: Message): TeamActivity[] {
  return message.activities?.filter((activity) => activity.status !== 'pending') ?? []
}

function debateStatus(event: ChatExpertEvent): string {
  if (event.role === 'synthesizer') {
    return event.event === 'expert_done' ? 'Finalizing answer' : 'Synthesizer is writing final answer'
  }
  return 'Debate team is working'
}

function initialStatus(value: ChatMode): string {
  if (value === 'expert') {
    return 'Starting expert team'
  }
  if (value === 'debate') {
    return 'Starting debate team'
  }
  return 'Contacting model'
}

function modeLabel(value: ChatMode): string {
  if (value === 'expert') {
    return 'Collaborate'
  }
  if (value === 'debate') {
    return 'Debate'
  }
  return 'Normal'
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
        <p>{{ modeLabel(mode) }}</p>
      </div>

      <div class="mode-toggle" aria-label="Chat mode">
        <button :class="{ selected: mode === 'normal' }" @click="mode = 'normal'">Normal</button>
        <button :class="{ selected: mode === 'expert' }" @click="mode = 'expert'">Collaborate</button>
        <button :class="{ selected: mode === 'debate' }" @click="mode = 'debate'">Debate</button>
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
          <div v-if="visibleActivities(message).length" class="activity-panel">
            <p class="activity-title">Team activity</p>
            <div
              v-for="activity in visibleActivities(message)"
              :key="activity.key"
              :class="['activity-row', activity.status]"
            >
              <span class="activity-marker" aria-hidden="true" />
              <span>{{ activity.label }}</span>
              <span v-if="activity.status === 'running'" class="typing-dots" aria-hidden="true">...</span>
            </div>
          </div>
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
            <summary>Team details</summary>
            <details v-for="expert in message.experts" :key="expert.role" class="expert-detail">
              <summary>
                <div class="expert-summary-content">
                  <div>
                    <h3>{{ expert.title }}</h3>
                    <span>{{ expert.providerName }} / {{ expert.model }}</span>
                  </div>
                  <span class="expert-status">{{ expert.done ? 'Done' : 'Running' }}</span>
                </div>
              </summary>
              <div class="expert-body">
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
              </div>
            </details>
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
