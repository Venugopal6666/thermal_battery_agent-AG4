import { useState, useRef, useEffect } from 'react';

export default function HistorySidebar({
  conversations,
  activeConversationId,
  onSelectConversation,
  onNewChat,
  onDeleteConversation,
  onRenameConversation,
  onPinConversation,
  onSearch,
  collapsed,
  onToggle,
}) {
  const [searchQuery, setSearchQuery] = useState('');
  const [menuOpenId, setMenuOpenId] = useState(null);
  const [renamingId, setRenamingId] = useState(null);
  const [renameValue, setRenameValue] = useState('');
  const menuRef = useRef(null);
  const renameInputRef = useRef(null);

  const handleSearch = (e) => {
    const q = e.target.value;
    setSearchQuery(q);
    onSearch(q);
  };

  const formatDate = (dateStr) => {
    const d = new Date(dateStr);
    const now = new Date();
    const diffMs = now - d;
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

    if (diffDays === 0) return 'Today';
    if (diffDays === 1) return 'Yesterday';
    if (diffDays < 7) return `${diffDays}d ago`;
    return d.toLocaleDateString('en-IN', { month: 'short', day: 'numeric' });
  };

  // Close menu when clicking outside
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setMenuOpenId(null);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Auto-focus rename input
  useEffect(() => {
    if (renamingId && renameInputRef.current) {
      renameInputRef.current.focus();
      renameInputRef.current.select();
    }
  }, [renamingId]);

  const handleMenuToggle = (e, convId) => {
    e.stopPropagation();
    setMenuOpenId(menuOpenId === convId ? null : convId);
  };

  const handleRenameStart = (e, conv) => {
    e.stopPropagation();
    setRenamingId(conv.id);
    setRenameValue(conv.title);
    setMenuOpenId(null);
  };

  const handleRenameSubmit = (convId) => {
    if (renameValue.trim() && renameValue.trim() !== '') {
      onRenameConversation(convId, renameValue.trim());
    }
    setRenamingId(null);
    setRenameValue('');
  };

  const handleRenameKeyDown = (e, convId) => {
    if (e.key === 'Enter') {
      handleRenameSubmit(convId);
    } else if (e.key === 'Escape') {
      setRenamingId(null);
      setRenameValue('');
    }
  };

  const handlePin = (e, convId) => {
    e.stopPropagation();
    onPinConversation(convId);
    setMenuOpenId(null);
  };

  const handleDelete = (e, convId) => {
    e.stopPropagation();
    onDeleteConversation(convId);
    setMenuOpenId(null);
  };

  if (collapsed) return null;

  // Separate pinned and unpinned
  const pinned = conversations.filter((c) => c.is_pinned);
  const unpinned = conversations.filter((c) => !c.is_pinned);

  const renderConversation = (conv) => (
    <div
      key={conv.id}
      className={`conversation-item ${conv.id === activeConversationId ? 'active' : ''} ${conv.is_pinned ? 'pinned' : ''}`}
      onClick={() => onSelectConversation(conv.id)}
    >
      {conv.is_pinned && <span className="pin-indicator" title="Pinned">📌</span>}

      {renamingId === conv.id ? (
        <input
          ref={renameInputRef}
          className="rename-input"
          value={renameValue}
          onChange={(e) => setRenameValue(e.target.value)}
          onKeyDown={(e) => handleRenameKeyDown(e, conv.id)}
          onBlur={() => handleRenameSubmit(conv.id)}
          onClick={(e) => e.stopPropagation()}
        />
      ) : (
        <span className="conv-title">{conv.title}</span>
      )}

      <span className="conv-date">{formatDate(conv.updated_at)}</span>

      <div className="conv-actions">
        <button
          className="menu-btn"
          onClick={(e) => handleMenuToggle(e, conv.id)}
          title="Options"
        >
          ⋯
        </button>

        {menuOpenId === conv.id && (
          <div className="context-menu" ref={menuRef}>
            <button
              className="context-menu-item"
              onClick={(e) => handleRenameStart(e, conv)}
            >
              <span className="menu-icon">✏️</span> Rename
            </button>
            <button
              className="context-menu-item"
              onClick={(e) => handlePin(e, conv.id)}
            >
              <span className="menu-icon">{conv.is_pinned ? '📌' : '📍'}</span>
              {conv.is_pinned ? 'Unpin' : 'Pin'}
            </button>
            <div className="context-menu-divider" />
            <button
              className="context-menu-item delete"
              onClick={(e) => handleDelete(e, conv.id)}
            >
              <span className="menu-icon">🗑️</span> Delete
            </button>
          </div>
        )}
      </div>
    </div>
  );

  return (
    <div className="sidebar">
      <div className="sidebar-header">
        <h2>Chat History</h2>
        <button className="toggle-btn" onClick={onToggle} title="Close sidebar">
          ✕
        </button>
      </div>

      <button className="new-chat-btn" onClick={onNewChat}>
        ＋ New Chat
      </button>

      <div className="sidebar-search">
        <input
          type="text"
          placeholder="Search conversations..."
          value={searchQuery}
          onChange={handleSearch}
        />
      </div>

      <div className="sidebar-conversations">
        {conversations.length === 0 && (
          <div style={{ padding: '16px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
            No conversations yet
          </div>
        )}

        {pinned.length > 0 && (
          <div className="conv-section">
            <div className="conv-section-label">📌 Pinned</div>
            {pinned.map(renderConversation)}
          </div>
        )}

        {unpinned.length > 0 && (
          <div className="conv-section">
            {pinned.length > 0 && <div className="conv-section-label">Recent</div>}
            {unpinned.map(renderConversation)}
          </div>
        )}
      </div>
    </div>
  );
}
