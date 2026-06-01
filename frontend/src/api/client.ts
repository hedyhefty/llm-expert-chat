const API_BASE = ''
const TOKEN_KEY = 'llm-expert-chat-token'

export type ChatMode = 'normal' | 'expert' | 'debate'

export type User = {
  id: string
  email: string
}

export type AuthResponse = {
  access_token: string
  token_type: 'bearer'
  user: User
}

export type Provider = {
  id: string
  name: string
  base_url: string
  model: string
  enabled: boolean
  created_at: string
}

export type ProviderPayload = {
  name: string
  base_url: string
  model: string
  api_key: string
  enabled: boolean
}

export type ProviderUpdatePayload = Partial<ProviderPayload>

export type ProviderTestResponse = {
  ok: boolean
  message: string
}

export type ProviderRef = {
  id: string
  name: string
  model: string
  enabled: boolean
}

export type ModelRoutingEffective = {
  normal: ProviderRef | null
  expert_planner: ProviderRef | null
  expert_providers: ProviderRef[]
  expert_reviewer: ProviderRef | null
  expert_synthesizer: ProviderRef | null
  debate_debaters: ProviderRef[]
  debate_synthesizer: ProviderRef | null
}

export type ModelRoutingPayload = {
  normal_provider_id: string | null
  expert_planner_provider_id: string | null
  expert_provider_ids: string[]
  expert_reviewer_provider_id: string | null
  expert_synthesizer_provider_id: string | null
  debate_debater_provider_ids: string[]
  debate_synthesizer_provider_id: string | null
}

export type ModelRouting = ModelRoutingPayload & {
  effective: ModelRoutingEffective
}

export type ConversationSummary = {
  id: string
  title: string
  created_at: string
  updated_at: string
}

export type ConversationExpert = {
  role: string
  title: string
  provider_name: string
  model: string
  content: string
  reasoning: string
  done: boolean
  error?: string | null
}

export type ConversationMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  status: string
  created_at: string
  mode?: ChatMode | null
  reasoning?: string
  experts?: ConversationExpert[]
}

export type ConversationDetail = ConversationSummary & {
  messages: ConversationMessage[]
}

export type ChatExpertEvent = {
  event: 'expert_start' | 'expert_delta' | 'expert_reasoning_delta' | 'expert_done'
  role: string
  title: string
  provider_name: string
  model: string
  content?: string
  reasoning?: string
  error?: string | null
}

export type ChatStreamMeta = {
  conversation_id: string
  user_message_id: string
  assistant_message_id: string
  mode: ChatMode
}

export type StreamChatOptions = {
  signal?: AbortSignal
  replaceAssistantMessageId?: string
  sourceUserMessageId?: string
  onMeta?: (meta: ChatStreamMeta) => void
}

export function getAuthToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setAuthToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearAuthToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

export async function healthCheck(): Promise<{ status: string }> {
  return apiFetch('/api/health')
}

export async function register(email: string, password: string): Promise<AuthResponse> {
  return apiFetch('/api/auth/register', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
}

export async function login(email: string, password: string): Promise<AuthResponse> {
  return apiFetch('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
}

export async function getMe(): Promise<User> {
  return apiFetch('/api/auth/me')
}

export async function listProviders(): Promise<Provider[]> {
  return apiFetch('/api/providers')
}

export async function createProvider(payload: ProviderPayload): Promise<Provider> {
  return apiFetch('/api/providers', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateProvider(id: string, payload: ProviderUpdatePayload): Promise<Provider> {
  return apiFetch(`/api/providers/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function deleteProvider(id: string): Promise<void> {
  await apiFetch(`/api/providers/${id}`, { method: 'DELETE' })
}

export async function testProvider(id: string): Promise<ProviderTestResponse> {
  return apiFetch(`/api/providers/${id}/test`, { method: 'POST' })
}

export async function getModelRouting(): Promise<ModelRouting> {
  return apiFetch('/api/providers/routing')
}

export async function updateModelRouting(payload: ModelRoutingPayload): Promise<ModelRouting> {
  return apiFetch('/api/providers/routing', {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export async function listConversations(): Promise<ConversationSummary[]> {
  return apiFetch('/api/conversations')
}

export async function createConversation(title: string): Promise<ConversationSummary> {
  return apiFetch('/api/conversations', {
    method: 'POST',
    body: JSON.stringify({ title }),
  })
}

export async function updateConversation(id: string, title: string): Promise<ConversationSummary> {
  return apiFetch(`/api/conversations/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  })
}

export async function deleteConversation(id: string): Promise<void> {
  await apiFetch(`/api/conversations/${id}`, { method: 'DELETE' })
}

export async function getConversation(id: string): Promise<ConversationDetail> {
  return apiFetch(`/api/conversations/${id}`)
}

export async function streamChat(
  message: string,
  mode: ChatMode,
  conversationId: string,
  onToken: (token: string) => void,
  onReasoning?: (token: string) => void,
  onExpertEvent?: (event: ChatExpertEvent) => void,
  options: StreamChatOptions = {},
): Promise<void> {
  const response = await fetch(`${API_BASE}/api/chat/stream`, {
    method: 'POST',
    headers: makeHeaders(),
    signal: options.signal,
    body: JSON.stringify({
      message,
      mode,
      conversation_id: conversationId,
      replace_assistant_message_id: options.replaceAssistantMessageId,
      source_user_message_id: options.sourceUserMessageId,
    }),
  })

  if (!response.ok || !response.body) {
    throw new Error(await readError(response, 'Chat request failed'))
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) {
      break
    }

    buffer += decoder.decode(value, { stream: true })
    const events = buffer.split('\n\n')
    buffer = events.pop() ?? ''

    for (const event of events) {
      handleSseEvent(event, onToken, onReasoning, onExpertEvent, options.onMeta)
    }
  }

  buffer += decoder.decode()
  if (buffer.trim()) {
    handleSseEvent(buffer, onToken, onReasoning, onExpertEvent, options.onMeta)
  }
}

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: makeHeaders(init.headers),
  })

  if (!response.ok) {
    throw new Error(await readError(response, 'Request failed'))
  }

  if (response.status === 204) {
    return undefined as T
  }

  return response.json()
}

function makeHeaders(headers?: HeadersInit): HeadersInit {
  const token = getAuthToken()
  return {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...headers,
  }
}

async function readError(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json()
    if (typeof body.detail === 'string') {
      return body.detail
    }
  } catch {
    // Ignore non-JSON error bodies.
  }
  return fallback
}

function handleSseEvent(
  event: string,
  onToken: (token: string) => void,
  onReasoning?: (token: string) => void,
  onExpertEvent?: (event: ChatExpertEvent) => void,
  onMeta?: (meta: ChatStreamMeta) => void,
): void {
  const dataLines: string[] = []
  let eventType = 'message'

  for (const line of event.split('\n')) {
    if (line.startsWith('event: ')) {
      eventType = line.slice(7)
      continue
    }

    if (!line.startsWith('data: ')) {
      continue
    }

    dataLines.push(line.slice(6))
  }

  if (!dataLines.length) {
    return
  }

  const token = dataLines.join('\n')
  if (token !== '[DONE]') {
    if (eventType === 'reasoning') {
      onReasoning?.(token)
    } else if (eventType === 'message') {
      onToken(token)
    } else if (eventType.startsWith('expert_')) {
      const expertEvent = parseExpertEvent(token)
      if (expertEvent) {
        onExpertEvent?.(expertEvent)
      }
    } else if (eventType === 'meta') {
      const meta = parseChatStreamMeta(token)
      if (meta) {
        onMeta?.(meta)
      }
    }
  }
}

function parseExpertEvent(token: string): ChatExpertEvent | null {
  try {
    return JSON.parse(token) as ChatExpertEvent
  } catch {
    return null
  }
}

function parseChatStreamMeta(token: string): ChatStreamMeta | null {
  try {
    return JSON.parse(token) as ChatStreamMeta
  } catch {
    return null
  }
}
