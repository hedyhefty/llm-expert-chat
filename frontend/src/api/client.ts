const API_BASE = ''
const TOKEN_KEY = 'llm-expert-chat-token'

export type ChatMode = 'normal' | 'expert'

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

export async function streamChat(
  message: string,
  mode: ChatMode,
  onToken: (token: string) => void,
  onReasoning?: (token: string) => void,
  onExpertEvent?: (event: ChatExpertEvent) => void,
): Promise<void> {
  const response = await fetch(`${API_BASE}/api/chat/stream`, {
    method: 'POST',
    headers: makeHeaders(),
    body: JSON.stringify({ message, mode }),
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
      handleSseEvent(event, onToken, onReasoning, onExpertEvent)
    }
  }

  buffer += decoder.decode()
  if (buffer.trim()) {
    handleSseEvent(buffer, onToken, onReasoning, onExpertEvent)
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
