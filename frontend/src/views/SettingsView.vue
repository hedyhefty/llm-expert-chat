<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import type { Provider, ProviderPayload, ProviderUpdatePayload } from '../api/client'
import {
  createProvider,
  deleteProvider,
  listProviders,
  testProvider,
  updateProvider,
} from '../api/client'

type ProviderDraft = {
  name: string
  baseUrl: string
  model: string
  apiKey: string
  enabled: boolean
}

const providers = ref<Provider[]>([])
const editingId = ref<string | null>(null)
const loading = ref(false)
const saving = ref(false)
const statusMessage = ref('')
const testMessages = reactive<Record<string, string>>({})
const draft = reactive<ProviderDraft>({
  name: '',
  baseUrl: 'https://api.openai.com/v1',
  model: '',
  apiKey: '',
  enabled: true,
})

onMounted(() => loadProviders())

async function loadProviders(clearStatus = true) {
  loading.value = true
  if (clearStatus) {
    statusMessage.value = ''
  }

  try {
    providers.value = await listProviders()
  } catch (error) {
    statusMessage.value = error instanceof Error ? error.message : 'Failed to load providers'
  } finally {
    loading.value = false
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
    await loadProviders(false)
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
    await loadProviders(false)
  } catch (error) {
    statusMessage.value = error instanceof Error ? error.message : 'Failed to delete provider'
  }
}

async function toggleProvider(provider: Provider) {
  try {
    await updateProvider(provider.id, { enabled: provider.enabled })
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

      <section class="provider-section">
        <div class="section-heading">
          <h2>Saved providers</h2>
          <button class="secondary-button" type="button" :disabled="loading" @click="() => loadProviders()">
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
