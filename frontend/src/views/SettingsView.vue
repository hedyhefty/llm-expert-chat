<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import type { ModelRouting, ModelRoutingPayload, Provider, ProviderPayload, ProviderRef, ProviderUpdatePayload } from '../api/client'
import {
  createProvider,
  deleteProvider,
  getModelRouting,
  listProviders,
  testProvider,
  updateProvider,
  updateModelRouting,
} from '../api/client'

type ProviderDraft = {
  name: string
  baseUrl: string
  model: string
  apiKey: string
  enabled: boolean
}

type RouteDraft = {
  normalProviderId: string
  expertPlannerProviderId: string
  expertProviderIds: string[]
  expertReviewerProviderId: string
  expertSynthesizerProviderId: string
  debateDebaterProviderIds: string[]
  debateSynthesizerProviderId: string
}

const providers = ref<Provider[]>([])
const routing = ref<ModelRouting | null>(null)
const editingId = ref<string | null>(null)
const loading = ref(false)
const saving = ref(false)
const savingRouting = ref(false)
const statusMessage = ref('')
const routingMessage = ref('')
const testMessages = reactive<Record<string, string>>({})
const draft = reactive<ProviderDraft>({
  name: '',
  baseUrl: 'https://api.openai.com/v1',
  model: '',
  apiKey: '',
  enabled: true,
})
const routingDraft = reactive<RouteDraft>({
  normalProviderId: '',
  expertPlannerProviderId: '',
  expertProviderIds: [],
  expertReviewerProviderId: '',
  expertSynthesizerProviderId: '',
  debateDebaterProviderIds: [],
  debateSynthesizerProviderId: '',
})

const enabledProviders = computed(() => providers.value.filter((provider) => provider.enabled))

onMounted(() => loadSettings())

async function loadSettings(clearStatus = true) {
  loading.value = true
  if (clearStatus) {
    statusMessage.value = ''
    routingMessage.value = ''
  }

  try {
    const [providerList, routeConfig] = await Promise.all([listProviders(), getModelRouting()])
    providers.value = providerList
    routing.value = routeConfig
    applyRouting(routeConfig)
  } catch (error) {
    statusMessage.value = error instanceof Error ? error.message : 'Failed to load settings'
  } finally {
    loading.value = false
  }
}

async function loadRouting(clearStatus = true) {
  if (clearStatus) {
    routingMessage.value = ''
  }

  try {
    const routeConfig = await getModelRouting()
    routing.value = routeConfig
    applyRouting(routeConfig)
  } catch (error) {
    routingMessage.value = error instanceof Error ? error.message : 'Failed to load routing'
  }
}

async function saveProvider() {
  statusMessage.value = ''
  saving.value = true

  try {
    if (editingId.value) {
      const payload: ProviderUpdatePayload = {
        name: draft.name,
        base_url: draft.baseUrl,
        model: draft.model,
        enabled: draft.enabled,
      }
      if (draft.apiKey.trim()) {
        payload.api_key = draft.apiKey
      }
      await updateProvider(editingId.value, payload)
      statusMessage.value = 'Provider updated'
    } else {
      if (!draft.apiKey.trim()) {
        statusMessage.value = 'API key is required'
        return
      }

      const payload: ProviderPayload = {
        name: draft.name,
        base_url: draft.baseUrl,
        model: draft.model,
        api_key: draft.apiKey,
        enabled: draft.enabled,
      }
      await createProvider(payload)
      statusMessage.value = 'Provider saved'
    }

    resetDraft()
    await loadSettings(false)
  } catch (error) {
    statusMessage.value = error instanceof Error ? error.message : 'Failed to save provider'
  } finally {
    saving.value = false
  }
}

function editProvider(provider: Provider) {
  editingId.value = provider.id
  draft.name = provider.name
  draft.baseUrl = provider.base_url
  draft.model = provider.model
  draft.apiKey = ''
  draft.enabled = provider.enabled
}

function resetDraft() {
  editingId.value = null
  draft.name = ''
  draft.baseUrl = 'https://api.openai.com/v1'
  draft.model = ''
  draft.apiKey = ''
  draft.enabled = true
}

async function removeProvider(provider: Provider) {
  statusMessage.value = ''

  try {
    await deleteProvider(provider.id)
    statusMessage.value = 'Provider deleted'
    await loadSettings(false)
  } catch (error) {
    statusMessage.value = error instanceof Error ? error.message : 'Failed to delete provider'
  }
}

async function toggleProvider(provider: Provider) {
  try {
    await updateProvider(provider.id, { enabled: provider.enabled })
    await loadRouting(false)
  } catch (error) {
    provider.enabled = !provider.enabled
    statusMessage.value = error instanceof Error ? error.message : 'Failed to update provider'
  }
}

async function runProviderTest(provider: Provider) {
  testMessages[provider.id] = 'Testing'

  try {
    const result = await testProvider(provider.id)
    testMessages[provider.id] = result.message
  } catch (error) {
    testMessages[provider.id] = error instanceof Error ? error.message : 'Connection test failed'
  }
}

async function saveRouting() {
  routingMessage.value = ''
  savingRouting.value = true

  try {
    const routeConfig = await updateModelRouting(toRoutingPayload())
    routing.value = routeConfig
    applyRouting(routeConfig)
    routingMessage.value = 'Routing saved'
  } catch (error) {
    routingMessage.value = error instanceof Error ? error.message : 'Failed to save routing'
  } finally {
    savingRouting.value = false
  }
}

function applyRouting(routeConfig: ModelRouting) {
  routingDraft.normalProviderId = routeConfig.normal_provider_id ?? ''
  routingDraft.expertPlannerProviderId = routeConfig.expert_planner_provider_id ?? ''
  routingDraft.expertProviderIds = [...routeConfig.expert_provider_ids]
  routingDraft.expertReviewerProviderId = routeConfig.expert_reviewer_provider_id ?? ''
  routingDraft.expertSynthesizerProviderId = routeConfig.expert_synthesizer_provider_id ?? ''
  routingDraft.debateDebaterProviderIds = [...routeConfig.debate_debater_provider_ids]
  routingDraft.debateSynthesizerProviderId = routeConfig.debate_synthesizer_provider_id ?? ''
}

function toRoutingPayload(): ModelRoutingPayload {
  return {
    normal_provider_id: emptyToNull(routingDraft.normalProviderId),
    expert_planner_provider_id: emptyToNull(routingDraft.expertPlannerProviderId),
    expert_provider_ids: [...routingDraft.expertProviderIds],
    expert_reviewer_provider_id: emptyToNull(routingDraft.expertReviewerProviderId),
    expert_synthesizer_provider_id: emptyToNull(routingDraft.expertSynthesizerProviderId),
    debate_debater_provider_ids: [...routingDraft.debateDebaterProviderIds],
    debate_synthesizer_provider_id: emptyToNull(routingDraft.debateSynthesizerProviderId),
  }
}

function emptyToNull(value: string): string | null {
  return value || null
}

function providerLabel(provider: Provider | ProviderRef): string {
  return `${provider.name} / ${provider.model}`
}

function providerRefLabel(provider: ProviderRef | null): string {
  return provider ? providerLabel(provider) : 'Auto'
}

function providerListLabel(providerRefs: ProviderRef[]): string {
  return providerRefs.length ? providerRefs.map(providerLabel).join(', ') : 'Auto'
}

function isRouteProviderSelected(field: 'expertProviderIds' | 'debateDebaterProviderIds', providerId: string): boolean {
  return routingDraft[field].includes(providerId)
}

function toggleRouteProvider(field: 'expertProviderIds' | 'debateDebaterProviderIds', providerId: string) {
  const selectedIds = routingDraft[field]
  const index = selectedIds.indexOf(providerId)
  if (index >= 0) {
    selectedIds.splice(index, 1)
    return
  }

  selectedIds.push(providerId)
}
</script>

<template>
  <section class="settings-view">
    <header class="topbar">
      <div>
        <h1>Settings</h1>
        <p>Providers</p>
      </div>
    </header>

    <div class="settings-layout">
      <div class="settings-stack">
        <form class="settings-form" @submit.prevent="saveProvider">
          <h2>{{ editingId ? 'Edit provider' : 'Add provider' }}</h2>
          <label>
            Name
            <input v-model="draft.name" placeholder="OpenAI" required />
          </label>
          <label>
            Base URL
            <input v-model="draft.baseUrl" placeholder="https://api.openai.com/v1" required />
          </label>
          <label>
            Model
            <input v-model="draft.model" placeholder="gpt-4.1-mini" required />
          </label>
          <label>
            API Key
            <input
              v-model="draft.apiKey"
              type="password"
              :placeholder="editingId ? 'Leave blank to keep current key' : 'sk-...'"
              :required="!editingId"
            />
          </label>
          <label class="checkbox-row">
            <input v-model="draft.enabled" type="checkbox" />
            Enabled
          </label>

          <p v-if="statusMessage" class="form-note">{{ statusMessage }}</p>

          <div class="form-actions">
            <button type="submit" :disabled="saving">
              {{ saving ? 'Saving' : editingId ? 'Update provider' : 'Save provider' }}
            </button>
            <button v-if="editingId" class="secondary-button" type="button" @click="resetDraft">
              Cancel
            </button>
          </div>
        </form>

        <form class="routing-panel" @submit.prevent="saveRouting">
          <div class="section-heading">
            <h2>Model routing</h2>
            <button class="secondary-button" type="button" :disabled="loading" @click="() => loadRouting()">
              Reload
            </button>
          </div>

          <label>
            Normal
            <select v-model="routingDraft.normalProviderId">
              <option value="">Auto</option>
              <option v-for="provider in enabledProviders" :key="provider.id" :value="provider.id">
                {{ providerLabel(provider) }}
              </option>
            </select>
          </label>

          <div class="route-grid">
            <section class="route-group">
              <h3>Collaborate</h3>
              <label>
                Planner
                <select v-model="routingDraft.expertPlannerProviderId">
                  <option value="">Auto</option>
                  <option v-for="provider in enabledProviders" :key="provider.id" :value="provider.id">
                    {{ providerLabel(provider) }}
                  </option>
                </select>
              </label>

              <div class="route-field">
                <p>Expert pool</p>
                <div class="route-option-list">
                  <label v-for="provider in enabledProviders" :key="provider.id" class="route-option">
                    <input
                      type="checkbox"
                      :checked="isRouteProviderSelected('expertProviderIds', provider.id)"
                      @change="toggleRouteProvider('expertProviderIds', provider.id)"
                    />
                    <span>{{ providerLabel(provider) }}</span>
                  </label>
                </div>
              </div>

              <label>
                Reviewer
                <select v-model="routingDraft.expertReviewerProviderId">
                  <option value="">Auto</option>
                  <option v-for="provider in enabledProviders" :key="provider.id" :value="provider.id">
                    {{ providerLabel(provider) }}
                  </option>
                </select>
              </label>

              <label>
                Synthesizer
                <select v-model="routingDraft.expertSynthesizerProviderId">
                  <option value="">Auto</option>
                  <option v-for="provider in enabledProviders" :key="provider.id" :value="provider.id">
                    {{ providerLabel(provider) }}
                  </option>
                </select>
              </label>
            </section>

            <section class="route-group">
              <h3>Debate</h3>
              <div class="route-field">
                <p>Debater pool</p>
                <div class="route-option-list">
                  <label v-for="provider in enabledProviders" :key="provider.id" class="route-option">
                    <input
                      type="checkbox"
                      :checked="isRouteProviderSelected('debateDebaterProviderIds', provider.id)"
                      @change="toggleRouteProvider('debateDebaterProviderIds', provider.id)"
                    />
                    <span>{{ providerLabel(provider) }}</span>
                  </label>
                </div>
              </div>

              <label>
                Synthesizer
                <select v-model="routingDraft.debateSynthesizerProviderId">
                  <option value="">Auto</option>
                  <option v-for="provider in enabledProviders" :key="provider.id" :value="provider.id">
                    {{ providerLabel(provider) }}
                  </option>
                </select>
              </label>
            </section>
          </div>

          <div v-if="routing" class="route-effective">
            <p><strong>Normal:</strong> {{ providerRefLabel(routing.effective.normal) }}</p>
            <p><strong>Collaborate pool:</strong> {{ providerListLabel(routing.effective.expert_providers) }}</p>
            <p><strong>Debate pool:</strong> {{ providerListLabel(routing.effective.debate_debaters) }}</p>
          </div>

          <p v-if="routingMessage" class="form-note">{{ routingMessage }}</p>

          <div class="form-actions">
            <button type="submit" :disabled="savingRouting || !enabledProviders.length">
              {{ savingRouting ? 'Saving' : 'Save routing' }}
            </button>
          </div>
        </form>
      </div>

      <section class="provider-section">
        <div class="section-heading">
          <h2>Saved providers</h2>
          <button class="secondary-button" type="button" :disabled="loading" @click="() => loadSettings()">
            Refresh
          </button>
        </div>

        <p v-if="loading" class="empty-state">Loading providers</p>
        <p v-else-if="!providers.length" class="empty-state">No providers yet</p>

        <div v-else class="provider-list">
          <article v-for="provider in providers" :key="provider.id" class="provider-item">
            <div class="provider-main">
              <div>
                <h3>{{ provider.name }}</h3>
                <p>{{ provider.model }}</p>
                <span>{{ provider.base_url }}</span>
              </div>
              <label class="switch-row">
                <input v-model="provider.enabled" type="checkbox" @change="toggleProvider(provider)" />
                Enabled
              </label>
            </div>

            <p v-if="testMessages[provider.id]" class="test-message">
              {{ testMessages[provider.id] }}
            </p>

            <div class="provider-actions">
              <button class="secondary-button" type="button" @click="runProviderTest(provider)">
                Test
              </button>
              <button class="secondary-button" type="button" @click="editProvider(provider)">
                Edit
              </button>
              <button class="danger-button" type="button" @click="removeProvider(provider)">
                Delete
              </button>
            </div>
          </article>
        </div>
      </section>
    </div>
  </section>
</template>
