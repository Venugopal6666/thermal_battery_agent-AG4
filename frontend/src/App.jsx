import { useState, useEffect, useCallback } from 'react';
import HistorySidebar from './components/HistorySidebar';
import ChatArea from './components/ChatArea';
import RulebookPanel from './components/RulebookPanel';
import ModeSelector from './components/ModeSelector';
import * as api from './api';
import './index.css';

export default function App() {
  // ── State ──────────────────────────────────────────────────
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [rulebookCollapsed, setRulebookCollapsed] = useState(true);

  // Chat
  const [conversations, setConversations] = useState([]);
  const [activeConversationId, setActiveConversationId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [loadingConversationId, setLoadingConversationId] = useState(null);
  const [thinkingSteps, setThinkingSteps] = useState([]);
  const [mode, setMode] = useState('normal');

  // Rulebook
  const [rules, setRules] = useState([]);
  const [categories, setCategories] = useState([]);
  const [activeCategory, setActiveCategory] = useState(null);

  // ── Load conversations on mount ────────────────────────────
  useEffect(() => {
    loadConversations();
  }, []);

  // ── Load rules when panel opens ────────────────────────────
  useEffect(() => {
    if (!rulebookCollapsed) {
      loadRules();
      loadCategories();
    }
  }, [rulebookCollapsed, activeCategory]);

  // ── API Handlers ───────────────────────────────────────────

  const loadConversations = async () => {
    try {
      const data = await api.listConversations();
      setConversations(data);
    } catch (err) {
      console.error('Failed to load conversations:', err);
    }
  };

  const loadMessages = async (convId) => {
    try {
      const data = await api.getConversationMessages(convId);
      setMessages(data);
    } catch (err) {
      console.error('Failed to load messages:', err);
    }
  };

  const loadRules = async () => {
    try {
      const params = {};
      if (activeCategory) params.category = activeCategory;
      const data = await api.listRules(params);
      setRules(data);
    } catch (err) {
      console.error('Failed to load rules:', err);
    }
  };

  const loadCategories = async () => {
    try {
      const data = await api.getRuleCategories();
      setCategories(data);
    } catch (err) {
      console.error('Failed to load categories:', err);
    }
  };

  // ── Chat Actions ───────────────────────────────────────────

  const handleSendMessage = useCallback(async (text) => {
    // Optimistic UI — add user message immediately
    const tempUserMsg = {
      id: 'temp-user-' + Date.now(),
      role: 'user',
      content: text,
      mode,
      rules_used: [],
      bq_queries_run: [],
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, tempUserMsg]);
    setIsLoading(true);
    setLoadingConversationId(activeConversationId);
    setThinkingSteps([]);

    try {
      // Use streaming endpoint for real-time thinking display
      const response = await api.sendMessageStream(
        text,
        activeConversationId,
        mode,
        (step) => {
          setThinkingSteps((prev) => {
            if (prev.includes(step)) return prev;
            return [...prev, step];
          });
        }
      );

      if (!response) throw new Error('No response received from the agent');

      // If this was a new conversation, update the ID
      if (!activeConversationId && response.conversation_id) {
        setActiveConversationId(response.conversation_id);
        setLoadingConversationId(response.conversation_id);
      }

      // Add assistant response
      const assistantMsg = {
        id: 'msg-' + Date.now(),
        role: 'assistant',
        content: response.response,
        mode,
        rules_used: response.rules_used || [],
        bq_queries_run: response.bq_queries_run || [],
        thinking_content: response.thinking_content,
        thinking_steps: response.thinking_steps || [],
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, assistantMsg]);

      // Refresh conversation list
      loadConversations();
    } catch (err) {
      // Show error message
      const errorMsg = {
        id: 'error-' + Date.now(),
        role: 'assistant',
        content: `⚠️ Error: ${err.message}. Please make sure the backend is running.`,
        mode: 'normal',
        rules_used: [],
        bq_queries_run: [],
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setIsLoading(false);
      setLoadingConversationId(null);
      setThinkingSteps([]);
    }
  }, [activeConversationId, mode]);

  const handleNewChat = () => {
    setActiveConversationId(null);
    setMessages([]);
  };

  const handleSelectConversation = async (convId) => {
    setActiveConversationId(convId);
    // Don't show thinking state from a different conversation
    if (loadingConversationId !== convId) {
      setThinkingSteps([]);
    }
    await loadMessages(convId);
  };

  const handleDeleteConversation = async (convId) => {
    try {
      await api.deleteConversation(convId);
      if (activeConversationId === convId) {
        setActiveConversationId(null);
        setMessages([]);
      }
      loadConversations();
    } catch (err) {
      console.error('Failed to delete conversation:', err);
    }
  };

  const handleRenameConversation = async (convId, newTitle) => {
    try {
      await api.renameConversation(convId, newTitle);
      loadConversations();
    } catch (err) {
      console.error('Failed to rename conversation:', err);
    }
  };

  const handlePinConversation = async (convId) => {
    try {
      await api.togglePinConversation(convId);
      loadConversations();
    } catch (err) {
      console.error('Failed to pin conversation:', err);
    }
  };

  const handleSearchConversations = async (query) => {
    try {
      if (!query.trim()) {
        loadConversations();
        return;
      }
      const data = await api.searchConversations(query);
      setConversations(data);
    } catch (err) {
      console.error('Failed to search conversations:', err);
    }
  };

  // ── Rulebook Actions ───────────────────────────────────────

  const handleAddRule = async (data) => {
    try {
      await api.createRule(data);
      loadRules();
      loadCategories();
    } catch (err) {
      console.error('Failed to add rule:', err);
    }
  };

  const handleEditRule = async (ruleId, data) => {
    try {
      await api.updateRule(ruleId, data);
      loadRules();
      loadCategories();
    } catch (err) {
      console.error('Failed to update rule:', err);
    }
  };

  const handleDeleteRule = async (ruleId) => {
    if (!confirm('Delete this rule? It will also be removed from the vector database.')) return;
    try {
      await api.deleteRule(ruleId);
      loadRules();
      loadCategories();
    } catch (err) {
      console.error('Failed to delete rule:', err);
    }
  };

  const handleUploadDocument = async (file, category) => {
    try {
      await api.uploadRuleDocument(file, category);
      loadRules();
      loadCategories();
    } catch (err) {
      console.error('Failed to upload document:', err);
      alert(`Upload failed: ${err.message}`);
    }
  };

  // ── Render ─────────────────────────────────────────────────

  return (
    <div className="app">
      {/* History Sidebar */}
      <HistorySidebar
        conversations={conversations}
        activeConversationId={activeConversationId}
        onSelectConversation={handleSelectConversation}
        onNewChat={handleNewChat}
        onDeleteConversation={handleDeleteConversation}
        onRenameConversation={handleRenameConversation}
        onPinConversation={handlePinConversation}
        onSearch={handleSearchConversations}
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed(!sidebarCollapsed)}
      />

      {/* Main Chat Area */}
      <div className="main-area">
        <div className="chat-header">
          <div className="chat-header-left">
            {sidebarCollapsed && (
              <button className="toggle-btn" onClick={() => setSidebarCollapsed(false)} title="Open history">
                ☰
              </button>
            )}
            <span className="chat-title">🔋 RESL Battery Agent</span>
          </div>

          <div className="chat-header-right">
            <ModeSelector mode={mode} onModeChange={setMode} />
            <button
              className="toggle-btn"
              onClick={() => setRulebookCollapsed(!rulebookCollapsed)}
              title={rulebookCollapsed ? 'Open rulebook' : 'Close rulebook'}
              style={!rulebookCollapsed ? { borderColor: 'var(--accent-primary)', color: 'var(--accent-primary)' } : {}}
            >
              📋
            </button>
          </div>
        </div>

        <ChatArea
          messages={messages}
          isLoading={isLoading && (loadingConversationId === activeConversationId || loadingConversationId === null)}
          thinkingSteps={(isLoading && loadingConversationId !== activeConversationId && loadingConversationId !== null) ? [] : thinkingSteps}
          onSendMessage={handleSendMessage}
          mode={mode}
        />
      </div>

      {/* Rulebook Panel */}
      <RulebookPanel
        rules={rules}
        categories={categories}
        collapsed={rulebookCollapsed}
        onToggle={() => setRulebookCollapsed(!rulebookCollapsed)}
        onAddRule={handleAddRule}
        onEditRule={handleEditRule}
        onDeleteRule={handleDeleteRule}
        onUploadDocument={handleUploadDocument}
        activeCategory={activeCategory}
        onCategoryChange={setActiveCategory}
      />
    </div>
  );
}
