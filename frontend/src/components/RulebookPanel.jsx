import { useState, useRef } from 'react';

export default function RulebookPanel({
  rules,
  categories,
  collapsed,
  onToggle,
  onAddRule,
  onEditRule,
  onDeleteRule,
  onUploadDocument,
  activeCategory,
  onCategoryChange,
}) {
  const [showAddModal, setShowAddModal] = useState(false);
  const [showUploadModal, setShowUploadModal] = useState(false);
  const [editingRule, setEditingRule] = useState(null);

  if (collapsed) return null;

  return (
    <div className="rulebook-panel">
      <div className="panel-header">
        <h2>📋 Rulebook</h2>
        <button className="toggle-btn" onClick={onToggle} title="Close panel">
          ✕
        </button>
      </div>

      <div className="panel-actions">
        <button className="panel-btn primary" onClick={() => setShowAddModal(true)}>
          ＋ Add Rule
        </button>
        <button className="panel-btn" onClick={() => setShowUploadModal(true)}>
          📄 Upload
        </button>
      </div>

      {categories.length > 0 && (
        <div className="panel-filters">
          <button
            className={`category-filter ${!activeCategory ? 'active' : ''}`}
            onClick={() => onCategoryChange(null)}
          >
            All
          </button>
          {categories.map((cat) => (
            <button
              key={cat}
              className={`category-filter ${activeCategory === cat ? 'active' : ''}`}
              onClick={() => onCategoryChange(cat)}
            >
              {cat}
            </button>
          ))}
        </div>
      )}

      <div className="rules-list">
        {rules.length === 0 && (
          <div style={{ padding: '24px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
            No rules yet. Add rules manually or upload a document.
          </div>
        )}
        {rules.map((rule) => (
          <RuleCard
            key={rule.id}
            rule={rule}
            onEdit={() => setEditingRule(rule)}
            onDelete={() => onDeleteRule(rule.id)}
          />
        ))}
      </div>

      {/* Add Rule Modal */}
      {showAddModal && (
        <RuleModal
          title="Add New Rule"
          onClose={() => setShowAddModal(false)}
          onSave={(data) => {
            onAddRule(data);
            setShowAddModal(false);
          }}
        />
      )}

      {/* Edit Rule Modal */}
      {editingRule && (
        <RuleModal
          title="Edit Rule"
          initialData={editingRule}
          onClose={() => setEditingRule(null)}
          onSave={(data) => {
            onEditRule(editingRule.id, data);
            setEditingRule(null);
          }}
        />
      )}

      {/* Upload Modal */}
      {showUploadModal && (
        <UploadModal
          onClose={() => setShowUploadModal(false)}
          onUpload={(file, category) => {
            onUploadDocument(file, category);
            setShowUploadModal(false);
          }}
        />
      )}
    </div>
  );
}

function RuleCard({ rule, onEdit, onDelete }) {
  return (
    <div className={`rule-card ${!rule.is_active ? 'rule-card-inactive' : ''}`}>
      <div className="rule-card-header">
        <span className="rule-card-title">{rule.title}</span>
        <div className="rule-card-actions">
          <button onClick={onEdit} title="Edit">✏️</button>
          <button className="delete" onClick={onDelete} title="Delete">🗑</button>
        </div>
      </div>
      <div className="rule-card-content">{rule.content}</div>
      <div className="rule-card-meta">
        {rule.category && (
          <span className="rule-card-category">{rule.category}</span>
        )}
        <span className="rule-card-source">
          {rule.source === 'uploaded_document' ? `📄 ${rule.source_file || 'Document'}` : '✍️ Manual'}
        </span>
      </div>
    </div>
  );
}

function RuleModal({ title, initialData, onClose, onSave }) {
  const [formData, setFormData] = useState({
    title: initialData?.title || '',
    content: initialData?.content || '',
    category: initialData?.category || '',
    tags: initialData?.tags?.join(', ') || '',
    is_active: initialData?.is_active !== undefined ? initialData.is_active : true,
  });

  const handleSubmit = () => {
    if (!formData.title.trim() || !formData.content.trim()) return;
    onSave({
      title: formData.title.trim(),
      content: formData.content.trim(),
      category: formData.category.trim() || null,
      tags: formData.tags ? formData.tags.split(',').map(t => t.trim()).filter(Boolean) : [],
      is_active: formData.is_active,
    });
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>{title}</h3>

        <div className="form-group">
          <label>Rule Title *</label>
          <input
            type="text"
            value={formData.title}
            onChange={(e) => setFormData({ ...formData, title: e.target.value })}
            placeholder="e.g., Maximum Discharge Temperature Limit"
          />
        </div>

        <div className="form-group">
          <label>Rule Content *</label>
          <textarea
            value={formData.content}
            onChange={(e) => setFormData({ ...formData, content: e.target.value })}
            placeholder="Describe the rule in detail..."
          />
        </div>

        <div className="form-group">
          <label>Category</label>
          <select
            value={formData.category}
            onChange={(e) => setFormData({ ...formData, category: e.target.value })}
          >
            <option value="">Select category</option>
            <option value="safety">Safety</option>
            <option value="design">Design</option>
            <option value="assembly">Assembly</option>
            <option value="testing">Testing</option>
            <option value="quality">Quality</option>
            <option value="general">General</option>
          </select>
        </div>

        <div className="form-group">
          <label>Tags (comma-separated)</label>
          <input
            type="text"
            value={formData.tags}
            onChange={(e) => setFormData({ ...formData, tags: e.target.value })}
            placeholder="e.g., temperature, discharge, voltage"
          />
        </div>

        {initialData && (
          <div className="form-group">
            <label>
              <input
                type="checkbox"
                checked={formData.is_active}
                onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
                style={{ marginRight: '8px' }}
              />
              Active (visible to agent)
            </label>
          </div>
        )}

        <div className="modal-actions">
          <button className="btn-cancel" onClick={onClose}>Cancel</button>
          <button className="btn-save" onClick={handleSubmit}>
            {initialData ? 'Update' : 'Add Rule'}
          </button>
        </div>
      </div>
    </div>
  );
}

function UploadModal({ onClose, onUpload }) {
  const [file, setFile] = useState(null);
  const [category, setCategory] = useState('');
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef(null);

  const handleDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    const droppedFile = e.dataTransfer.files[0];
    if (droppedFile) setFile(droppedFile);
  };

  const handleSubmit = () => {
    if (!file) return;
    onUpload(file, category || null);
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>Upload Document</h3>

        <div
          className={`upload-zone ${dragging ? 'dragging' : ''}`}
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
        >
          <div className="upload-icon">📁</div>
          {file ? (
            <p><strong>{file.name}</strong> ({(file.size / 1024).toFixed(1)} KB)</p>
          ) : (
            <>
              <p>Drop a file here or click to browse</p>
              <div className="file-types">Supported: PDF, DOCX, TXT</div>
            </>
          )}
        </div>
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.docx,.txt"
          style={{ display: 'none' }}
          onChange={(e) => setFile(e.target.files[0])}
        />

        <div className="form-group" style={{ marginTop: '16px' }}>
          <label>Category for extracted rules</label>
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">Auto-detect</option>
            <option value="safety">Safety</option>
            <option value="design">Design</option>
            <option value="assembly">Assembly</option>
            <option value="testing">Testing</option>
            <option value="quality">Quality</option>
            <option value="general">General</option>
          </select>
        </div>

        <div className="modal-actions">
          <button className="btn-cancel" onClick={onClose}>Cancel</button>
          <button className="btn-save" onClick={handleSubmit} disabled={!file}>
            Upload & Extract Rules
          </button>
        </div>
      </div>
    </div>
  );
}
