import { useState } from 'react';

export default function ModeSelector({ mode, onModeChange }) {
  const modes = [
    { key: 'normal', label: 'Normal', icon: '💬' },
    { key: 'deep_think', label: 'Deep Think', icon: '🧠' },
    { key: 'deep_research', label: 'Deep Research', icon: '🔬' },
  ];

  return (
    <div className="mode-selector">
      {modes.map((m) => (
        <button
          key={m.key}
          className={`mode-btn ${mode === m.key ? 'active' : ''}`}
          onClick={() => onModeChange(m.key)}
          title={m.label}
        >
          {m.icon} {m.label}
        </button>
      ))}
    </div>
  );
}
