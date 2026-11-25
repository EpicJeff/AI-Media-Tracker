import {
  LitElement,
  html,
  css
} from 'https://unpkg.com/lit-element@2.4.0/lit-element.js?module';

class AiMediaRouterCard extends LitElement {
  static get properties() {
    return {
      hass: {},
      config: {},
      _selectedSource: { type: String },
      _previewToggles: { type: Object },
      _draggedSource: { type: String },
      _draggedDest: { type: String },
      _expandedGroupParent: { type: String }
    };
  }

  constructor() {
    super();
    this._selectedSource = null;
    this._previewToggles = {};
    this._draggedSource = null;
    this._draggedDest = null;
    this._expandedGroupParent = null;
  }

  static async getConfigElement() {
    return document.createElement('ai-media-router-card-editor');
  }

  static getStubConfig() {
    return { layout: 'auto' };
  }

  setConfig(config) {
    if (!config.entities && !config.sources && !config.destinations) {
      // Allow empty config for auto-discovery
    }
    this.config = config;
  }

  shouldUpdate(changedProps) {
    if (changedProps.has('_selectedSource')) return true;
    if (changedProps.has('_previewToggles')) return true;
    if (changedProps.has('_draggedSource')) return true;
    if (changedProps.has('_draggedDest')) return true;
    if (changedProps.has('_expandedGroupParent')) return true;
    if (changedProps.has('hass')) return true;
    return false;
  }

  _getSources() {
    if (!this.hass) return [];
    const states = Object.values(this.hass.states);
    // Find our AI Media Tracker sources
    return states.filter(s => s.entity_id.startsWith('media_player.ai_media_'));
  }

  _getDestinations() {
    if (!this.hass) return [];
    
    // Get all allowed cast targets from all AI media sources
    const sources = this._getSources();
    let allowedTargets = new Set();
    let hasExplicitTargets = false;
    
    sources.forEach(source => {
      const targets = source.attributes.cast_targets || [];
      if (targets.length > 0) {
        hasExplicitTargets = true;
        targets.forEach(t => allowedTargets.add(t));
      }
    });

    // Get all groups to filter out children
    const allChildren = new Set();
    sources.forEach(source => {
      const groups = source.attributes.target_groups || {};
      Object.values(groups).forEach(children => children.forEach(c => allChildren.add(c)));
    });

    const states = Object.values(this.hass.states);
    
    return states.filter(s => {
      if (!s.entity_id.startsWith('media_player.')) return false;
      if (s.entity_id.startsWith('media_player.ai_media_')) return false;
      if (hasExplicitTargets && !allowedTargets.has(s.entity_id)) return false;
      if (allChildren.has(s.entity_id)) return false; // Filter out children from main list
      return s.state !== 'unavailable';
    }).sort((a, b) => (a.attributes.friendly_name || a.entity_id).localeCompare(b.attributes.friendly_name || b.entity_id));
  }

  _isDestinationActive(sourceId, destId) {
    const sourceState = this.hass.states[sourceId];
    if (!sourceState) return false;
    const activeCasts = sourceState.attributes.active_casts || [];
    return activeCasts.includes(destId);
  }

  _togglePreview(sourceId, e) {
    e.stopPropagation();
    this._previewToggles = {
      ...this._previewToggles,
      [sourceId]: !this._previewToggles[sourceId]
    };
  }

  _handleSourceClick(sourceId) {
    if (this._selectedSource === sourceId) {
      this._selectedSource = null;
    } else {
      this._selectedSource = sourceId;
    }
  }

  _handleDestClick(destId) {
    if (!this._selectedSource) {
      // If no source is selected, but the destination is active, stop it
      const sources = this._getSources();
      for (const source of sources) {
        if (this._isDestinationActive(source.entity_id, destId)) {
          this.hass.callService("ai_media_tracker", "stop_cast", {
            entity_id: source.entity_id,
            target_receivers: [destId]
          });
          return;
        }
      }
      return;
    }

    const isActive = this._isDestinationActive(this._selectedSource, destId);
    
    if (isActive) {
      this.hass.callService("ai_media_tracker", "stop_cast", {
        entity_id: this._selectedSource,
        target_receivers: [destId]
      });
    } else {
      this.hass.callService("ai_media_tracker", "follow_me", {
        entity_id: this._selectedSource,
        target_receivers: [destId]
      });
    }
    
    // Auto-deselect after route if desired, but keeping it selected is better for multi-routing
  }

  // Drag and Drop Handlers
  _handleDragStart(e, sourceId) {
    this._draggedSource = sourceId;
    e.dataTransfer.setData('text/plain', 'source:' + sourceId);
    e.dataTransfer.effectAllowed = 'copyMove';
  }

  _handleDragEnd(e) {
    this._draggedSource = null;
  }

  _handleDestDragStart(e, destId) {
    this._draggedDest = destId;
    e.dataTransfer.setData('text/plain', 'dest:' + destId);
    e.dataTransfer.effectAllowed = 'copyMove';
  }

  _handleDestDragEnd(e) {
    this._draggedDest = null;
  }

  _handleDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  }

  _handleDrop(e, targetDestId) {
    e.preventDefault();
    const data = e.dataTransfer.getData('text/plain');
    if (!data) return;
    
    if (data.startsWith('source:')) {
      const sourceId = data.substring(7);
      const isActive = this._isDestinationActive(sourceId, targetDestId);
      if (!isActive) {
        this.hass.callService("ai_media_tracker", "follow_me", {
          entity_id: sourceId,
          target_receivers: [targetDestId]
        });
      }
    } else if (data.startsWith('dest:')) {
      const draggedDestId = data.substring(5);
      if (draggedDestId && draggedDestId !== targetDestId) {
        // Group dragged dest under target dest
        this.hass.callService("ai_media_tracker", "group_targets", {
          parent_entity_id: targetDestId,
          child_entity_ids: [draggedDestId]
        });
      }
    }
  }

  // Color generator based on source index for visual linking
  _getSourceColor(index) {
    const colors = [
      '#2ecc71', // Unify to Green
      '#00f2fe', // Neon Blue
      '#ff0844', // Neon Pink
      '#f6d365', // Neon Yellow
      '#b3ffab', // Mint
      '#ff9a44'  // Orange
    ];
    return colors[index % colors.length];
  }

  render() {
    if (!this.hass) return html``;

    const sources = this._getSources();
    const destinations = this._getDestinations();

    return html`
      <ha-card class="router-card">
        <div class="header">
          <div class="title">AV Matrix Router</div>
          <div class="subtitle">Drag & Drop to Route Streams</div>
        </div>
        
        <div class="layout-container layout-${this.config.layout || 'auto'}">
          
          <!-- SOURCES COLUMN -->
          <div class="sources-column">
            <div class="section-title">Sources</div>
            <div class="sources-list">
              ${sources.map((source, index) => {
                const color = this._getSourceColor(index);
                const isSelected = this._selectedSource === source.entity_id;
                const isDragged = this._draggedSource === source.entity_id;
                const isPreview = this._previewToggles[source.entity_id];
                
                const bgImage = source.attributes.entity_picture 
                  ? `url(${source.attributes.entity_picture})` 
                  : (source.attributes.logo_url ? `url(${source.attributes.logo_url})` : 'none');
                
                return html`
                  <div class="source-card ${isSelected ? 'selected' : ''} ${isDragged ? 'dragged' : ''}"
                       style="--accent-color: ${color};"
                       draggable="true"
                       @dragstart="${(e) => this._handleDragStart(e, source.entity_id)}"
                       @dragend="${this._handleDragEnd}"
                       @click="${() => this._handleSourceClick(source.entity_id)}">
                    
                    ${bgImage !== 'none' && !isPreview ? html`
                      <div class="source-bg" style="background-image: ${bgImage};"></div>
                    ` : ''}
                    
                    ${isPreview ? html`
                      <div class="source-bg">
                        <ha-camera-stream 
                          .hass="${this.hass}" 
                          .stateObj="${this.hass.states['camera.' + source.entity_id.replace('media_player.', '')]}"
                          muted="true"
                          style="width:100%; height:100%; object-fit:cover; position:absolute; top:0; left:0;">
                        </ha-camera-stream>
                      </div>
                    ` : ''}
                    
                    <div class="source-overlay"></div>
                    
                    <div class="source-content">
                      <div class="source-info">
                        <ha-icon icon="mdi:video-input-hdmi" class="source-icon"></ha-icon>
                        <div class="source-text">
                          <div class="source-name">${source.attributes.friendly_name || source.entity_id}</div>
                          <div class="source-state">${source.state.charAt(0).toUpperCase() + source.state.slice(1)} ${source.attributes.app_name ? '• ' + source.attributes.app_name : ''}</div>
                        </div>
                      </div>
                      
                      <div class="preview-toggle" @click="${(e) => this._togglePreview(source.entity_id, e)}" title="Toggle Live Preview">
                        <ha-icon icon="${isPreview ? 'mdi:image' : 'mdi:cctv'}"></ha-icon>
                      </div>
                    </div>
                  </div>
                `;
              })}
            </div>
          </div>
          
          <!-- DESTINATIONS COLUMN -->
          <div class="destinations-column">
            <div class="section-title">Destinations</div>
            <div class="destinations-grid">
              ${destinations.map(dest => {
                // Check if this destination is active for ANY source to color it
                let activeSourceIndex = -1;
                let activeSourceId = null;
                sources.forEach((s, i) => {
                  if (this._isDestinationActive(s.entity_id, dest.entity_id)) {
                    activeSourceIndex = i;
                    activeSourceId = s.entity_id;
                  }
                });
                
                const isActive = activeSourceIndex !== -1;
                const color = isActive ? this._getSourceColor(activeSourceIndex) : 'rgba(255,255,255,0.1)';
                
                let icon = 'mdi:television';
                if (dest.attributes.device_class === 'speaker') icon = 'mdi:speaker';
                else if (dest.attributes.device_class === 'receiver') icon = 'mdi:audio-video';

                // Check if this destination is a group parent
                let childCount = 0;
                sources.forEach(source => {
                  const groups = source.attributes.target_groups || {};
                  if (groups[dest.entity_id]) {
                    childCount = Math.max(childCount, groups[dest.entity_id].length);
                  }
                });
                const isGroup = childCount > 0;
                
                const isDraggedDest = this._draggedDest === dest.entity_id;
                
                return html`
                  <div class="dest-card ${isActive ? 'active' : ''} ${isDraggedDest ? 'dragged' : ''}"
                       style="--border-color: ${color};"
                       draggable="true"
                       @dragstart="${(e) => this._handleDestDragStart(e, dest.entity_id)}"
                       @dragend="${this._handleDestDragEnd}"
                       @dragover="${this._handleDragOver}"
                       @drop="${(e) => this._handleDrop(e, dest.entity_id)}"
                       @click="${() => this._handleDestClick(dest.entity_id)}">
                    
                    <div class="dest-icon-wrapper" style="color: ${isActive ? color : '#888'}; position:relative;">
                      <ha-icon icon="${icon}"></ha-icon>
                      ${isGroup ? html`
                        <div style="position:absolute; bottom:-8px; right:-8px; background:rgba(0,0,0,0.9); border:2px solid ${isActive ? color : '#666'}; color:#fff; font-size:0.85rem; width:28px; height:28px; display:flex; justify-content:center; align-items:center; border-radius:50%; font-weight:bold; cursor:pointer; box-shadow:0 2px 5px rgba(0,0,0,0.5); z-index:10;"
                             @click="${(e) => { e.stopPropagation(); this._expandedGroupParent = dest.entity_id; }}">
                          +${childCount}
                        </div>
                      ` : ''}
                    </div>
                    
                    <div class="dest-info">
                      <div class="dest-name">${dest.attributes.friendly_name || dest.entity_id}</div>
                      <div class="dest-state">${dest.state} ${isGroup ? html`<span style="color:#aaa;">(Group)</span>` : ''}</div>
                    </div>
                    
                    ${isActive ? html`
                      <div class="dest-badge" style="background: ${color}; color: #000;">
                        <ha-icon icon="mdi:link" style="--mdc-icon-size: 14px;"></ha-icon>
                      </div>
                    ` : ''}
                  </div>
                `;
              })}
            </div>
          </div>
          
        </div>
        
        ${this._expandedGroupParent ? (() => {
          const parent = destinations.find(d => d.entity_id === this._expandedGroupParent);
          if (!parent) return html``;
          
          let childIds = [];
          sources.forEach(source => {
            const groups = source.attributes.target_groups || {};
            if (groups[parent.entity_id]) childIds = groups[parent.entity_id];
          });
          
          if (childIds.length === 0) {
            setTimeout(() => { if (this._expandedGroupParent === parent.entity_id) this._expandedGroupParent = null; }, 0);
            return html``;
          }
          
          const allStates = Object.values(this.hass.states);
          const children = childIds.map(id => allStates.find(s => s.entity_id === id)).filter(Boolean);
          
          return html`
            <div style="position:absolute; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.8); backdrop-filter:blur(5px); z-index:100; display:flex; justify-content:center; align-items:center;" @click="${() => this._expandedGroupParent = null}">
              <div style="background:#1a1a1c; border:1px solid rgba(255,255,255,0.1); border-radius:12px; padding:24px; width:300px; max-width:90%; box-shadow:0 10px 40px rgba(0,0,0,0.5);" @click="${e => e.stopPropagation()}">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
                  <h3 style="margin:0; font-size:1.1rem;">${parent.attributes.friendly_name || parent.entity_id}</h3>
                  <ha-icon icon="mdi:close" style="cursor:pointer; color:#888;" @click="${() => this._expandedGroupParent = null}"></ha-icon>
                </div>
                
                <div style="display:flex; flex-direction:column; gap:8px; max-height:300px; overflow-y:auto;">
                  ${children.map(child => html`
                    <div style="display:flex; justify-content:space-between; align-items:center; background:rgba(255,255,255,0.05); padding:10px 12px; border-radius:8px;">
                      <span style="font-size:0.9rem; font-weight:500;">${child.attributes.friendly_name || child.entity_id}</span>
                      <button style="background:rgba(231,76,60,0.2); color:#e74c3c; border:1px solid #e74c3c; border-radius:4px; padding:4px 8px; font-size:0.75rem; cursor:pointer;"
                              @click="${() => { this.hass.callService('ai_media_tracker', 'ungroup_targets', { child_entity_ids: [child.entity_id] }); }}">
                        Remove
                      </button>
                    </div>
                  `)}
                  ${children.length === 0 ? html`<div style="color:#888; font-size:0.85rem; text-align:center; padding:10px;">No devices in group.</div>` : ''}
                </div>
              </div>
            </div>
          `;
        })() : ''}
        
      </ha-card>
    `;
  }

  static get styles() {
    return css`
      :host {
        display: block;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      }
      .router-card {
        background: #111112;
        border-radius: 16px;
        overflow: hidden;
        border: 1px solid rgba(255, 255, 255, 0.05);
        display: flex;
        flex-direction: column;
        color: #fff;
      }
      .header {
        padding: 20px;
        border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        background: linear-gradient(to right, rgba(255,255,255,0.02), transparent);
      }
      .title {
        font-size: 1.2rem;
        font-weight: 600;
        letter-spacing: 0.5px;
      }
      .subtitle {
        font-size: 0.85rem;
        color: #888;
        margin-top: 4px;
      }
      
      .layout-container {
        display: flex;
        flex-direction: column;
        padding: 20px;
        gap: 30px;
      }
      
      @media (min-width: 768px) {
        .layout-container:not(.layout-stack) {
          flex-direction: row;
        }
        .layout-container:not(.layout-stack) .sources-column {
          width: 300px;
          flex-shrink: 0;
          border-right: 1px solid rgba(255,255,255,0.05);
          padding-right: 30px;
        }
        .layout-container:not(.layout-stack) .destinations-column {
          flex-grow: 1;
        }
      }
      
      .layout-split {
        flex-direction: row !important;
      }
      .layout-split .sources-column {
        width: 300px;
        flex-shrink: 0;
        border-right: 1px solid rgba(255,255,255,0.05);
        padding-right: 30px;
      }
      .layout-split .destinations-column {
        flex-grow: 1;
      }
      
      .layout-split .sources-list {
        flex-direction: column !important;
        overflow-x: visible !important;
        padding-bottom: 0 !important;
      }
      .layout-split .source-card {
        width: 100% !important;
      }
      
      .section-title {
        font-size: 0.85rem;
        text-transform: uppercase;
        letter-spacing: 1px;
        color: #666;
        margin-bottom: 16px;
        font-weight: 600;
      }
      
      /* SOURCES */
      .sources-list {
        display: flex;
        flex-direction: column;
        gap: 16px;
      }
      
      @media (max-width: 767px) {
        .sources-list {
          flex-direction: row;
          overflow-x: auto;
          padding-bottom: 10px;
          scroll-snap-type: x mandatory;
        }
        .source-card {
          width: 250px;
          flex-shrink: 0;
          scroll-snap-align: start;
        }
      }
      
      .source-card {
        position: relative;
        height: 120px;
        border-radius: 12px;
        overflow: hidden;
        cursor: grab;
        background: #1a1a1c;
        border: 2px solid transparent;
        transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);
        box-shadow: 0 4px 15px rgba(0,0,0,0.2);
      }
      .source-card:active {
        cursor: grabbing;
      }
      .source-card.selected {
        border-color: var(--accent-color);
        box-shadow: 0 0 20px var(--accent-color);
        transform: translateY(-2px);
      }
      .source-card.dragged {
        opacity: 0.5;
        transform: scale(0.95);
      }
      
      .source-bg {
        position: absolute;
        top: -10%; left: -10%; width: 120%; height: 120%;
        background-size: cover;
        background-position: center;
        filter: blur(8px) brightness(0.6);
        z-index: 1;
        transition: all 0.5s ease;
      }
      .source-overlay {
        position: absolute;
        top: 0; left: 0; width: 100%; height: 100%;
        background: linear-gradient(to top, rgba(0,0,0,0.8), rgba(0,0,0,0.2));
        z-index: 2;
      }
      
      .source-content {
        position: absolute;
        bottom: 0; left: 0; width: 100%;
        padding: 12px;
        box-sizing: border-box;
        z-index: 3;
        display: flex;
        justify-content: space-between;
        align-items: flex-end;
      }
      
      .source-info {
        display: flex;
        align-items: center;
        gap: 10px;
      }
      .source-icon {
        color: #fff;
        background: rgba(255,255,255,0.2);
        backdrop-filter: blur(4px);
        padding: 8px;
        border-radius: 50%;
        --mdc-icon-size: 20px;
      }
      .source-text {
        display: flex;
        flex-direction: column;
      }
      .source-name {
        font-weight: 600;
        font-size: 0.95rem;
        text-shadow: 0 2px 4px rgba(0,0,0,0.8);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        max-width: 140px;
      }
      .source-state {
        font-size: 0.75rem;
        color: rgba(255,255,255,0.7);
      }
      
      .preview-toggle {
        background: rgba(0,0,0,0.5);
        border: 1px solid rgba(255,255,255,0.2);
        backdrop-filter: blur(4px);
        padding: 6px;
        border-radius: 8px;
        cursor: pointer;
        color: #fff;
        transition: all 0.2s;
      }
      .preview-toggle:hover {
        background: rgba(255,255,255,0.2);
      }
      
      /* DESTINATIONS */
      .destinations-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
        gap: 16px;
      }
      
      .dest-card {
        background: rgba(255,255,255,0.03);
        border: 2px solid var(--border-color);
        border-radius: 12px;
        padding: 16px;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 12px;
        cursor: pointer;
        transition: all 0.3s;
        position: relative;
        overflow: hidden;
      }
      .dest-card:hover {
        background: rgba(255,255,255,0.06);
        transform: translateY(-2px);
      }
      .dest-card.active {
        background: rgba(255,255,255,0.08);
        box-shadow: inset 0 0 20px rgba(0,0,0,0.5);
      }
      .dest-card.dragged {
        opacity: 0.5;
        transform: scale(0.95);
      }
      
      .dest-icon-wrapper {
        background: rgba(0,0,0,0.3);
        padding: 16px;
        border-radius: 50%;
        transition: color 0.3s;
        --mdc-icon-size: 32px;
      }
      
      .dest-info {
        text-align: center;
      }
      .dest-name {
        font-weight: 500;
        font-size: 0.95rem;
      }
      .dest-state {
        font-size: 0.75rem;
        color: #888;
        margin-top: 4px;
        text-transform: capitalize;
      }
      
      .dest-badge {
        position: absolute;
        top: 8px;
        right: 8px;
        width: 24px;
        height: 24px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        box-shadow: 0 2px 5px rgba(0,0,0,0.5);
      }
    `;
  }
}

customElements.define('ai-media-router-card', AiMediaRouterCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: 'ai-media-router-card',
  name: 'AI Media Router',
  description: 'A premium AVoIP routing matrix with Drag & Drop functionality.'
});

class AiMediaRouterCardEditor extends LitElement {
  static get properties() {
    return {
      hass: {},
      _config: {}
    };
  }

  setConfig(config) {
    this._config = config;
  }

  _valueChanged(ev) {
    if (!this._config || !this.hass) {
      return;
    }
    const target = ev.target;
    if (this[`_${target.configValue}`] === target.value) {
      return;
    }
    if (target.configValue) {
      this._config = {
        ...this._config,
        [target.configValue]: target.value,
      };
    }
    const event = new Event("config-changed", {
      bubbles: true,
      composed: true,
    });
    event.detail = { config: this._config };
    this.dispatchEvent(event);
  }

  render() {
    if (!this.hass || !this._config) {
      return html``;
    }

    return html`
      <div class="card-config">
        <div class="option">
          <label>Layout Style</label>
          <select
            .configValue=${"layout"}
            @change=${this._valueChanged}
            .value=${this._config.layout || "auto"}
          >
            <option value="auto">Auto (Responsive)</option>
            <option value="split">Split Screen (Landscape Forced)</option>
            <option value="stack">Stacked (Portrait Forced)</option>
          </select>
          <div class="help-text">Auto will adapt based on the screen size. Split Screen puts sources on the left. Stacked puts sources in a horizontal row on top.</div>
        </div>
      </div>
    `;
  }

  static get styles() {
    return css`
      .card-config {
        display: flex;
        flex-direction: column;
        gap: 16px;
      }
      .option {
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      label {
        font-weight: 500;
      }
      select {
        padding: 8px;
        border-radius: 4px;
        border: 1px solid #ccc;
      }
      .help-text {
        font-size: 0.8rem;
        color: #888;
      }
    `;
  }
}

customElements.define('ai-media-router-card-editor', AiMediaRouterCardEditor);
