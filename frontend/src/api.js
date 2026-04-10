/**
 * API client for the RESL Thermal Battery Agent backend.
 */

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

async function request(path, options = {}) {
  const url = `${API_URL}${path}`;
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || 'Request failed');
  }
  return res.json();
}

// ── Chat ────────────────────────────────────────────────────

export async function sendMessage(message, conversationId = null, mode = 'normal') {
  return request('/api/chat/send', {
    method: 'POST',
    body: JSON.stringify({ message, conversation_id: conversationId, mode }),
  });
}

/**
 * Send a message using SSE streaming — receives real-time thinking steps.
 * @param {string} message
 * @param {string|null} conversationId
 * @param {string} mode
 * @param {function} onThinkingStep - called with each thinking step string
 * @returns {Promise<object>} - the final response object
 */
export async function sendMessageStream(message, conversationId = null, mode = 'normal', onThinkingStep) {
  const url = `${API_URL}/api/chat/send-stream`;
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, conversation_id: conversationId, mode }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || 'Request failed');
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let finalResult = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // Process complete SSE messages
    const lines = buffer.split('\n');
    buffer = lines.pop(); // Keep incomplete line in buffer

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const data = JSON.parse(line.slice(6));
          if (data.type === 'thinking') {
            onThinkingStep?.(data.step);
          } else if (data.type === 'conversation_id') {
            // Store conversation_id for later
            if (!finalResult) finalResult = {};
            finalResult.conversation_id = data.conversation_id;
          } else if (data.type === 'done') {
            finalResult = { ...finalResult, ...data };
          }
        } catch (e) {
          // Skip malformed JSON
        }
      }
    }
  }

  return finalResult;
}

export async function createConversation() {
  return request('/api/chat/new', { method: 'POST' });
}

export async function getConversationMessages(conversationId) {
  return request(`/api/chat/${conversationId}`);
}

export async function deleteConversation(conversationId) {
  return request(`/api/chat/${conversationId}`, { method: 'DELETE' });
}

// ── History ─────────────────────────────────────────────────

export async function listConversations(skip = 0, limit = 50) {
  return request(`/api/history/?skip=${skip}&limit=${limit}`);
}

export async function searchConversations(query) {
  return request(`/api/history/search?q=${encodeURIComponent(query)}`);
}

export async function renameConversation(conversationId, title) {
  return request(`/api/history/${conversationId}/rename`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  });
}

export async function togglePinConversation(conversationId) {
  return request(`/api/history/${conversationId}/pin`, {
    method: 'PATCH',
  });
}

// ── Rulebook ────────────────────────────────────────────────

export async function listRules(params = {}) {
  const searchParams = new URLSearchParams();
  if (params.category) searchParams.set('category', params.category);
  if (params.is_active !== undefined) searchParams.set('is_active', params.is_active);
  if (params.search) searchParams.set('search', params.search);
  const qs = searchParams.toString();
  return request(`/api/rules/${qs ? '?' + qs : ''}`);
}

export async function createRule(data) {
  return request('/api/rules/', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateRule(ruleId, data) {
  return request(`/api/rules/${ruleId}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export async function deleteRule(ruleId) {
  return request(`/api/rules/${ruleId}`, { method: 'DELETE' });
}

export async function getRuleCategories() {
  return request('/api/rules/categories');
}

export async function uploadRuleDocument(file, category = null) {
  const formData = new FormData();
  formData.append('file', file);
  if (category) formData.append('category', category);
  const url = `${API_URL}/api/rules/upload`;
  const res = await fetch(url, { method: 'POST', body: formData });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || 'Upload failed');
  }
  return res.json();
}

export async function rebuildVectors() {
  return request('/api/rules/rebuild-vectors', { method: 'POST' });
}
