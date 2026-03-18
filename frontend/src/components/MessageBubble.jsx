import { useState, useMemo, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import { exportToDocx } from '../utils/exportDocx';
import {
  LineChart, Line, BarChart, Bar, AreaChart, Area,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ScatterChart, Scatter,
} from 'recharts';

// Chart color palette — vibrant, accessible, premium
const CHART_COLORS = [
  '#6366f1', '#22d3ee', '#f59e0b', '#ef4444',
  '#10b981', '#8b5cf6', '#ec4899', '#14b8a6',
];

/**
 * Parse a ```chart code block into chart config.
 * Supports formats:
 *   ```chart
 *   type: line|bar|area|scatter
 *   title: Chart Title
 *   xKey: time_seconds
 *   yKeys: voltage_volts, current_amps
 *   data:
 *   time_seconds | voltage_volts | current_amps
 *   0            | 2.5           | 1.2
 *   10           | 2.4           | 1.3
 *   ```
 */
function parseChartBlock(code) {
  try {
    const lines = code.trim().split('\n');
    const config = { type: 'line', title: '', xKey: '', yKeys: [], data: [] };
    let inData = false;
    let headers = [];

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;

      if (!inData) {
        if (trimmed.toLowerCase().startsWith('type:')) {
          config.type = trimmed.split(':').slice(1).join(':').trim().toLowerCase();
        } else if (trimmed.toLowerCase().startsWith('title:')) {
          config.title = trimmed.split(':').slice(1).join(':').trim();
        } else if (trimmed.toLowerCase().startsWith('xkey:')) {
          config.xKey = trimmed.split(':').slice(1).join(':').trim();
        } else if (trimmed.toLowerCase().startsWith('ykeys:')) {
          config.yKeys = trimmed.split(':').slice(1).join(':').trim().split(',').map(k => k.trim());
        } else if (trimmed.toLowerCase().startsWith('data:')) {
          inData = true;
        }
      } else {
        // Parse table-like data rows
        const cells = trimmed.split('|').map(c => c.trim()).filter(Boolean);
        if (headers.length === 0) {
          headers = cells;
        } else if (!trimmed.match(/^[-|]+$/)) {
          // Skip separator lines like --- | --- | ---
          const row = {};
          cells.forEach((cell, i) => {
            const key = headers[i] || `col${i}`;
            const num = parseFloat(cell);
            row[key] = isNaN(num) ? cell : num;
          });
          config.data.push(row);
        }
      }
    }

    // Auto-detect xKey and yKeys if not set
    if (headers.length > 0) {
      if (!config.xKey) config.xKey = headers[0];
      if (config.yKeys.length === 0) config.yKeys = headers.slice(1);
    }

    return config.data.length > 0 ? config : null;
  } catch {
    return null;
  }
}

/**
 * Render a chart from config.
 */
function ChartRenderer({ config }) {
  const { type, title, xKey, yKeys, data } = config;

  const chartContent = useMemo(() => {
    const commonProps = {
      data,
      margin: { top: 5, right: 20, left: 10, bottom: 5 },
    };

    const xAxis = <XAxis dataKey={xKey} stroke="#94a3b8" fontSize={11} />;
    const yAxis = <YAxis stroke="#94a3b8" fontSize={11} />;
    const grid = <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.15)" />;
    const tooltip = (
      <Tooltip
        contentStyle={{
          background: '#1e1b4b',
          border: '1px solid rgba(99,102,241,0.3)',
          borderRadius: '8px',
          fontSize: '12px',
          color: '#e2e8f0',
        }}
      />
    );
    const legend = <Legend wrapperStyle={{ fontSize: '11px' }} />;

    switch (type) {
      case 'bar':
        return (
          <BarChart {...commonProps}>
            {grid}{xAxis}{yAxis}{tooltip}{legend}
            {yKeys.map((key, i) => (
              <Bar key={key} dataKey={key} fill={CHART_COLORS[i % CHART_COLORS.length]} radius={[4, 4, 0, 0]} />
            ))}
          </BarChart>
        );
      case 'area':
        return (
          <AreaChart {...commonProps}>
            {grid}{xAxis}{yAxis}{tooltip}{legend}
            {yKeys.map((key, i) => (
              <Area
                key={key} dataKey={key} type="monotone"
                stroke={CHART_COLORS[i % CHART_COLORS.length]}
                fill={CHART_COLORS[i % CHART_COLORS.length]}
                fillOpacity={0.15}
              />
            ))}
          </AreaChart>
        );
      case 'scatter':
        return (
          <ScatterChart {...commonProps}>
            {grid}{xAxis}{yAxis}{tooltip}{legend}
            {yKeys.map((key, i) => (
              <Scatter key={key} dataKey={key} fill={CHART_COLORS[i % CHART_COLORS.length]} name={key} />
            ))}
          </ScatterChart>
        );
      default: // line
        return (
          <LineChart {...commonProps}>
            {grid}{xAxis}{yAxis}{tooltip}{legend}
            {yKeys.map((key, i) => (
              <Line
                key={key} dataKey={key} type="monotone"
                stroke={CHART_COLORS[i % CHART_COLORS.length]}
                strokeWidth={2} dot={false} activeDot={{ r: 4 }}
              />
            ))}
          </LineChart>
        );
    }
  }, [type, xKey, yKeys, data]);

  return (
    <div className="chart-container">
      {title && <div className="chart-title">{title}</div>}
      <ResponsiveContainer width="100%" height={300}>
        {chartContent}
      </ResponsiveContainer>
    </div>
  );
}

/**
 * Custom code block renderer — handles chart blocks and syntax highlighting.
 */
function CodeBlock({ node, inline, className, children, ...props }) {
  const match = /language-(\w+)/.exec(className || '');
  const lang = match ? match[1] : '';
  const code = String(children).replace(/\n$/, '');

  // Check if it's a chart block
  if (lang === 'chart') {
    const config = parseChartBlock(code);
    if (config) {
      return <ChartRenderer config={config} />;
    }
  }

  // Inline code
  if (inline) {
    return <code className="inline-code" {...props}>{children}</code>;
  }

  // Regular code block
  return (
    <div className="code-block-wrapper">
      {lang && <div className="code-lang-badge">{lang}</div>}
      <pre className={className} {...props}>
        <code>{children}</code>
      </pre>
    </div>
  );
}

/**
 * Custom table renderer — styled with the dark theme.
 */
function MarkdownTable({ children }) {
  return (
    <div className="table-container">
      <table className="md-table">{children}</table>
    </div>
  );
}


export default function MessageBubble({ message, previousMessage }) {
  const [showRules, setShowRules] = useState(true);
  const [showThinking, setShowThinking] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const isUser = message.role === 'user';
  const isAssistant = message.role === 'assistant';

  const handleDownload = useCallback(async () => {
    setIsDownloading(true);
    try {
      const userQuestion = previousMessage?.content || '';
      await exportToDocx(message, userQuestion);
    } catch (err) {
      console.error('Download failed:', err);
      alert('Failed to generate document. Please try again.');
    } finally {
      setIsDownloading(false);
    }
  }, [message, previousMessage]);

  const modeBadge = message.mode && message.mode !== 'normal' && (
    <span className={`mode-badge ${message.mode === 'deep_think' ? 'deep-think' : 'deep-research'}`}>
      {message.mode === 'deep_think' ? '🧠 Deep Think' : '🔬 Deep Research'}
    </span>
  );

  return (
    <div className={`message ${message.role}`}>
      <div className="message-avatar">
        {isUser ? 'U' : '🔋'}
      </div>
      <div className="message-content">
        <div className="message-bubble markdown-body">
          {isUser ? (
            <p>{message.content}</p>
          ) : (
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              rehypePlugins={[rehypeRaw]}
              components={{
                code: CodeBlock,
                table: MarkdownTable,
              }}
            >
              {message.content || ''}
            </ReactMarkdown>
          )}
        </div>

        {isAssistant && modeBadge}

        {/* Rules Applied Section — always visible when rules exist */}
        {isAssistant && message.rules_used && message.rules_used.length > 0 && (
          <div className="rules-applied">
            <div
              className="rules-applied-header"
              onClick={() => setShowRules(!showRules)}
            >
              <span className="rules-icon">📋</span>
              <span className="rules-title">Rules Applied ({message.rules_used.length})</span>
              <span className="rules-toggle">{showRules ? '▼' : '▶'}</span>
            </div>
            <div className={`rules-applied-list ${showRules ? 'expanded' : 'collapsed'}`}>
              {message.rules_used.map((rule, i) => (
                <div key={i} className="rule-badge">
                  <span className="rule-badge-icon">📖</span>
                  <span className="rule-badge-text">{rule}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Thinking Content (Deep Think) */}
        {isAssistant && message.thinking_content && (
          <div className="thinking-section">
            <div
              className="thinking-header"
              onClick={() => setShowThinking(!showThinking)}
            >
              🧠 Reasoning Process
              <span>{showThinking ? '▼' : '▶'}</span>
            </div>
            {showThinking && (
              <div className="thinking-content">{message.thinking_content}</div>
            )}
          </div>
        )}
        {/* Download Button */}
        {isAssistant && message.content && (
          <button
            className={`download-docx-btn ${isDownloading ? 'downloading' : ''}`}
            onClick={handleDownload}
            disabled={isDownloading}
            title="Download as Word document"
          >
            {isDownloading ? (
              <>
                <span className="download-spinner" />
                Generating...
              </>
            ) : (
              <>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
                  <polyline points="7 10 12 15 17 10" />
                  <line x1="12" y1="15" x2="12" y2="3" />
                </svg>
                Download DOCX
              </>
            )}
          </button>
        )}
      </div>
    </div>
  );
}
