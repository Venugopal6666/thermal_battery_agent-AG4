import { useState, useRef, useEffect } from 'react';
import MessageBubble from './MessageBubble';

export default function ChatArea({ messages, isLoading, thinkingSteps = [], onSendMessage, mode }) {
  const [input, setInput] = useState('');
  const messagesEndRef = useRef(null);
  const textareaRef = useRef(null);
  const thinkingStepsRef = useRef(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isLoading, thinkingSteps]);

  // Auto-scroll thinking steps container to bottom when new steps appear
  useEffect(() => {
    if (thinkingStepsRef.current) {
      thinkingStepsRef.current.scrollTop = thinkingStepsRef.current.scrollHeight;
    }
  }, [thinkingSteps]);

  const handleSend = () => {
    const trimmed = input.trim();
    if (!trimmed || isLoading) return;
    onSendMessage(trimmed);
    setInput('');
    // Reset textarea height
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleInput = (e) => {
    setInput(e.target.value);
    // Auto-resize textarea
    const el = e.target;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 150) + 'px';
  };

  const modeLabels = {
    normal: 'Ask about your thermal batteries...',
    deep_think: 'Ask a complex question for in-depth reasoning...',
    deep_research: 'Describe what you want to investigate across builds...',
  };

  return (
    <>
      <div className="messages-area">
        {messages.length === 0 && !isLoading && (
          <div className="empty-state">
            <div className="empty-icon">🔋</div>
            <h3>RESL Thermal Battery Agent</h3>
            <p>
              Ask about your batteries, builds, discharge data, design parameters,
              or any thermal battery related question. I'll check the rulebook
              and provide data-backed answers.
            </p>
          </div>
        )}

        {messages.map((msg, i) => (
          <MessageBubble
            key={msg.id || i}
            message={msg}
            previousMessage={i > 0 ? messages[i - 1] : null}
          />
        ))}

        {isLoading && (
          <div className="message assistant">
            <div className="message-avatar">🔋</div>
            <div className="message-content">
              <div className="thinking-bubble">
                <div className="thinking-header-live">
                  <div className="thinking-pulse"></div>
                  <span>Thinking...</span>
                </div>
                {thinkingSteps.length > 0 && (
                  <div className="thinking-steps-live" ref={thinkingStepsRef}>
                    {thinkingSteps.map((step, i) => (
                      <div
                        key={i}
                        className={`thinking-step ${i === thinkingSteps.length - 1 ? 'active' : 'done'}`}
                      >
                        <span className="step-icon">
                          {i === thinkingSteps.length - 1 ? '⏳' : '✓'}
                        </span>
                        <span className="step-text">{step}</span>
                      </div>
                    ))}
                  </div>
                )}
                {thinkingSteps.length === 0 && (
                  <div className="loading-dots">
                    <span></span>
                    <span></span>
                    <span></span>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      <div className="chat-input-area">
        <div className="chat-input-container">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            placeholder={modeLabels[mode] || modeLabels.normal}
            rows={1}
          />
          <button
            className="send-btn"
            onClick={handleSend}
            disabled={!input.trim() || isLoading}
            title="Send message"
          >
            ➤
          </button>
        </div>
      </div>
    </>
  );
}
