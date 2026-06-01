<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import type {
  ChatExpertEvent,
  ChatMode,
  ConversationExpert,
  ConversationMessage,
  ConversationSummary,
  ModelRouting,
  ProviderRef,
} from '../api/client'
import { createConversation, getConversation, getModelRouting, streamChat } from '../api/client'
import { renderMarkdown } from '../utils/markdown'

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

const props = defineProps<{
  conversationId: string | null
}>()
const emit = defineEmits<{
  conversationCreated: [conversation: ConversationSummary]
  conversationUpdated: [conversationId: string]
}>()

const mode = ref<ChatMode>('normal')
const input = ref('')
const loading = ref(false)
const loadingHistory = ref(false)
const routing = ref<ModelRouting | null>(null)
const currentConversationId = ref<string | null>(null)
const messages = ref<Message[]>(readyMessages())
const activeRouteLabel = computed(() => modeRouteLabel(mode.value))

onMounted(() => {
  void loadRouting()
})

watch(
  () => props.conversationId,
  async (conversationId) => {
    if (conversationId === currentConversationId.value) {
      return
    }
    currentConversationId.value = conversationId
    await loadConversationMessages(conversationId)
  },
  { immediate: true },
)

async function sendMessage() {
  const content = input.value.trim()
  if (!content || loading.value) {
    return
  }

  let conversationId = currentConversationId.value
  if (!conversationId) {
    const conversation = await createConversation(conversationTitle(content))
    conversationId = conversation.id
    currentConversationId.value = conversation.id
    emit('conversationCreated', conversation)
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
      conversationId,
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
    emit('conversationUpdated', conversationId)
  }
}

async function loadConversationMessages(conversationId: string | null) {
  if (!conversationId) {
    messages.value = readyMessages()
    return
  }

  loadingHistory.value = true
  messages.value = [{ id: 'loading', role: 'assistant', content: 'Loading conversation...' }]
  try {
    const conversation = await getConversation(conversationId)
    messages.value = conversation.messages.length
      ? conversation.messages.map(savedMessageToMessage)
      : readyMessages()
  } catch (error) {
    messages.value = [
      {
        id: 'load-error',
        role: 'assistant',
        content: error instanceof Error ? error.message : 'Failed to load conversation',
      },
    ]
  } finally {
    loadingHistory.value = false
  }
}

async function loadRouting() {
  try {
    routing.value = await getModelRouting()
  } catch {
    routing.value = null
  }
}

function readyMessages(): Message[] {
  return [
    {
      id: 'welcome',
      role: 'assistant',
      content: 'Ready.',
    },
  ]
}

function savedMessageToMessage(message: ConversationMessage): Message {
  return {
    id: message.id,
    role: message.role,
    content: message.content,
    mode: message.mode ?? undefined,
    reasoning: message.reasoning || undefined,
    experts: message.experts?.map(savedExpertToDetail),
  }
}

function savedExpertToDetail(expert: ConversationExpert): ExpertDetail {
  return {
    role: expert.role,
    title: expert.title,
    providerName: expert.provider_name,
    model: expert.model,
    content: expert.content,
    reasoning: expert.reasoning,
    done: expert.done,
    error: expert.error ?? undefined,
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

function orderedExperts(message: Message): ExpertDetail[] {
  const experts = message.experts ?? []
  return [...experts].sort((left, right) => {
    const leftKey = expertSortKey(left.role, message.mode)
    const rightKey = expertSortKey(right.role, message.mode)
    return (
      leftKey.group - rightKey.group ||
      leftKey.index - rightKey.index ||
      leftKey.role.localeCompare(rightKey.role)
    )
  })
}

function expertSortKey(role: string, mode?: ChatMode): { group: number; index: number; role: string } {
  if (mode === 'debate') {
    const debaterMatch = role.match(/^debater_(\d+)$/)
    if (debaterMatch) {
      return { group: 0, index: Number(debaterMatch[1]), role }
    }

    const responseMatch = role.match(/^debater_(\d+)_response$/)
    if (responseMatch) {
      return { group: 1, index: Number(responseMatch[1]), role }
    }

    if (role === 'synthesizer') {
      return { group: 2, index: 0, role }
    }

    return { group: 99, index: 0, role }
  }

  if (role === 'planner') {
    return { group: 0, index: 0, role }
  }

  const expertMatch = role.match(/^expert_(\d+)$/)
  if (expertMatch) {
    return { group: 1, index: Number(expertMatch[1]), role }
  }

  if (role === 'reviewer') {
    return { group: 2, index: 0, role }
  }

  if (role === 'synthesizer') {
    return { group: 3, index: 0, role }
  }

  return { group: 99, index: 0, role }
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

function modeRouteLabel(value: ChatMode): string {
  const effective = routing.value?.effective
  if (!effective) {
    return ''
  }

  if (value === 'normal') {
    return providerLabel(effective.normal)
  }
  if (value === 'expert') {
    const expertCount = effective.expert_providers.length || 0
    return `${expertCount || 'Auto'} experts · Synth: ${providerLabel(effective.expert_synthesizer)}`
  }

  const debaterCount = effective.debate_debaters.length || 0
  return `${debaterCount || 'Auto'} debaters · Synth: ${providerLabel(effective.debate_synthesizer)}`
}

function providerLabel(provider: ProviderRef | null): string {
  return provider ? `${provider.name} / ${provider.model}` : 'Auto'
}

function conversationTitle(content: string): string {
  const title = content.replace(/\s+/g, ' ').trim()
  return title ? title.slice(0, 60) : 'New chat'
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
        <p>{{ modeLabel(mode) }}<span v-if="activeRouteLabel"> · {{ activeRouteLabel }}</span></p>
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
            <details v-for="expert in orderedExperts(message)" :key="expert.role" class="expert-detail">
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
      <textarea v-model="input" rows="3" placeholder="Message" :disabled="loadingHistory" />
      <button type="submit" :disabled="loading || loadingHistory || !input.trim()">
        {{ loading ? 'Sending' : 'Send' }}
      </button>
    </form>
  </section>
</template>
