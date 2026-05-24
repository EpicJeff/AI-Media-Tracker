import {
  LitElement,
  html,
  css
} from 'https://unpkg.com/lit-element@2.4.0/lit-element.js?module';

// ============================================================================
// 1. MAIN CARD ELEMENT (FULL-BLEED DAKBOARD DESIGN)
// ============================================================================
class AiMediaMatrixCard extends LitElement {
  static get properties() {
    return {
      hass: {},
      config: {},
      _activeTab: { type: String }
    };
  }

  static async getConfigElement() {
    return document.createElement('ai-media-matrix-card-editor');
  }

  static getStubConfig() {
    return {
      rooms: []
    };
  }

  constructor() {
    super();
    this._activeTab = '';
  }

  setConfig(config) {
    this.config = config || { rooms: [] };
    // Safely establish initial tab state out of the render pipeline
    if (this.config.rooms?.length > 0) {
      const paths = this.config.rooms.map(r => r.name);
      if (!this._activeTab || !paths.includes(this._activeTab)) {
        this._activeTab = this.config.rooms[0].name;
      }
    }
  }

  getCardSize() {
    return 8;
  }

  static get styles() {
    return css`
      :host {
        display: block;
        width: 100%;
        height: calc(100vh - var(--header-height));
        margin: 0;
        padding: 0;
        box-sizing: border-box;
        background: #09090a;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        -webkit-font-smoothing: antialiased;
        overflow: hidden;
      }

      /* Full Viewport Canvas Wrapper */
      .dak-fullscreen-wrapper {
        position: relative;
        width: 100%;
        height: calc(100vh - var(--header-height));
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        box-sizing: border-box;
        padding: 48px 64px;
      }

      @media (max-width: 600px) {
        .dak-fullscreen-wrapper {
          padding: 24px 32px;
        }
      }

      /* Edge-to-Edge Dynamic Poster Background */
      .canvas-background {
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background-size: cover;
        background-position: center;
        background-repeat: no-repeat;
        z-index: 0;
        transition: background-image 0.8s cubic-bezier(0.25, 1, 0.5, 1);
      }

      /* Deep, elegant vignette to secure typography contrast */
      .canvas-overlay {
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100vh;
        background: radial-gradient(circle at center, rgba(17,17,18,0.2) 0%, rgba(9,9,10,0.75) 100%);
        z-index: 1;
      }

      /* Layout Layer to force content above background canvas */
      .ui-layer {
        position: relative;
        z-index: 2;
        width: 100%;
      }

      /* Top Header: Navigation Controls */
      .top-nav-zone {
        display: flex;
        justify-content: flex-start;
      }

      .dak-tabs {
        display: flex;
        flex-wrap: wrap;
        gap: 28px;
      }

      .dak-tab-btn {
        background: transparent;
        color: rgba(255, 255, 255, 0.35);
        border: none;
        padding: 8px;
        font-size: 0.8rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 2px;
        cursor: pointer;
        border: 2px solid rgba(255, 255, 255, 0.33);
        border-radius: 8px;
        transition: all 0.25s ease;
      }

      .dak-tab-btn:hover {
        color: rgba(255, 255, 255, 0.8);
      }

      .dak-tab-btn.active {
        color: rgb(255, 255, 255);
        background-color: color-mix(in srgb, var(--vlm-accent, #ffffff) 65%, transparent);
        text-shadow: rgba(0, 0, 0, 0.3) 0px 2px 4px;
        border: 0px solid transparent;
      }

      /* Bottom Hero: Asymmetrical Typographic Split Stack */
      .bottom-display-zone {
        display: grid;
        grid-template-columns: 1.2fr 1fr;
        align-items: flex-end;
        gap: 48px;
        width: 100%;
      }

      @media (max-width: 900px) {
        .bottom-display-zone {
          grid-template-columns: 1fr;
          gap: 24px;
        }
      }

      /* Primary Meta (Left-justified) */
      .media-meta-stack {
        display: flex;
        flex-direction: column;
        gap: 8px;
      }

      .source-line {
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 3px;
        font-weight: 700;
        color: var(--vlm-accent, #a1a1a6);
        text-shadow: 0 1px 2px rgba(0,0,0,0.5);
      }

      .main-title {
        font-size: 3.5rem;
        font-weight: 300;
        line-height: 1.1;
        color: #ffffff;
        margin: 0;
        letter-spacing: -1px;
        text-shadow: 0 2px 8px rgba(0,0,0,0.5);
      }

      .sub-title {
        font-size: 1.5rem;
        font-weight: 400;
        color: #c5c5c7;
        margin: 4px 0 0 0;
        line-height: 1.3;
        text-shadow: 0 1px 4px rgba(0,0,0,0.5);
      }

      /* Secondary Telemetry Stack (Right-justified) */
      .vlm-telemetry-stack {
        display: flex;
        flex-direction: column;
        gap: 16px;
      }

      .dak-sports-panel {
        border-top: 1px solid rgba(255, 255, 255, 0.15);
        border-bottom: 1px solid rgba(255, 255, 255, 0.15);
        padding: 16px 0;
        display: flex;
        align-items: center;
        justify-content: space-between;
      }

      .dak-team-block {
        display: flex;
        align-items: baseline;
        gap: 12px;
      }

      .dak-team-name {
        font-size: 1.3rem;
        font-weight: 600;
        color: #ffffff;
        text-shadow: 0 1px 3px rgba(0,0,0,0.4);
      }

      .dak-team-score {
        font-size: 1.6rem;
        font-weight: 300;
        color: var(--vlm-accent, #ffffff);
        font-family: -apple-system-font, monospace;
        text-shadow: 0 1px 3px rgba(0,0,0,0.4);
      }

      .dak-sports-meta {
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 1.5px;
        color: #a1a1a6;
        text-align: right;
        line-height: 1.4;
      }

      .dak-tag-row {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
      }

      .dak-badge {
        font-size: 0.65rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 1px;
        background: rgba(0, 0, 0, 0.4);
        color: #e5e5ea;
        padding: 4px 10px;
        border-radius: 4px;
        border: 1px solid rgba(255, 255, 255, 0.12);
        backdrop-filter: blur(4px);
        -webkit-backdrop-filter: blur(4px);
      }

      .narrative-placard {
        border-top: 1px dashed rgba(255, 255, 255, 0.2);
        padding-top: 14px;
      }

      .placard-header {
        font-size: 0.7rem;
        text-transform: uppercase;
        letter-spacing: 2px;
        font-weight: 700;
        color: var(--vlm-accent, #a1a1a6);
        margin-bottom: 6px;
        text-shadow: 0 1px 2px rgba(0,0,0,0.4);
      }

      .placard-body {
        font-size: 0.95rem;
        line-height: 1.5;
        color: #e5e5ea;
        font-weight: 400;
        text-shadow: 0 1px 4px rgba(0,0,0,0.5);
      }

      .dak-standby {
        margin: auto;
        text-align: center;
        display: flex;
        flex-direction: column;
        gap: 8px;
      }

      .dak-standby-title {
        font-size: 2.5rem;
        font-weight: 300;
        color: rgba(255, 255, 255, 0.5);
        letter-spacing: -0.5px;
      }

      .dak-standby-sub {
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 3px;
        color: rgba(255, 255, 255, 0.3);
      }

      .empty-box {
        margin: auto;
        text-align: center;
        color: #86868b;
      }
    `;
  }

  render() {
    if (!this.hass || !this.config) return html``;

    const rooms = this.config.rooms || [];
    if (rooms.length === 0) {
      return html`
        <div class="dak-fullscreen-wrapper">
          <div class="empty-box">
            <h3 style="font-weight: 300; color: #fff; font-size: 1.5rem;">Matrix Screen Offline</h3>
            <p style="font-size: 0.9rem; margin-top: 4px;">Assign integration channels inside card parameters.</p>
          </div>
        </div>
      `;
    }

    // Safe lookup fallback without assigning to global class scope during render
    let currentRoom = rooms.find(r => r.name === this._activeTab) || rooms[0];

    const entityState = this.hass.states[currentRoom.entity];
    const isPlaying = entityState && entityState.state !== 'off' && entityState.state !== 'unavailable' && entityState.state !== 'idle';
    
    const title = entityState ? entityState.attributes.media_title || entityState.state : 'System Standby';
    const series = entityState ? entityState.attributes.media_series_title || '' : '';
    const posterUrl = entityState ? entityState.attributes.entity_picture : null;
    
    const appName = entityState?.attributes.extra_attributes?.app_name || entityState?.attributes.app_name || 'Display Platform';
    const vlmModel = entityState?.attributes.vlm_model || null;
    const confidence = entityState?.attributes.confidence_score ? Math.round(entityState.attributes.confidence_score * 100) : null;
    const contentType = entityState?.attributes.content_type || 'generic';
    
    const sceneAnalysis = entityState?.attributes.scene_analysis || {};
    const liveTelemetry = entityState?.attributes.live_telemetry || null;

    if (entityState && sceneAnalysis.primary_colors?.length > 0) {
      this.style.setProperty('--vlm-accent', sceneAnalysis.primary_colors[0]);
    } else {
      this.style.setProperty('--vlm-accent', '#ffffff');
    }

    return html`
      ${isPlaying && posterUrl ? html`
        <div class="canvas-background" style="background-image: url('${posterUrl}');"></div>
      ` : html`
        <div class="canvas-background" style="background-color: #0c0c0d;"></div>
      `}
      <div class="canvas-overlay"></div>

      <div class="dak-fullscreen-wrapper">
        
        <div class="ui-layer top-nav-zone">
          <div class="dak-tabs">
            ${rooms.map(room => html`
              <button 
                class="dak-tab-btn ${currentRoom.name === room.name ? 'active' : ''}"
                @click="${() => this._activeTab = room.name}"
              >
                ${room.name}
              </button>
            `)}
          </div>
        </div>

        ${isPlaying ? html`
          <div class="ui-layer bottom-display-zone">
            
            <div class="media-meta-stack">
              <div style="display: flex; align-items: center; gap: 20px; margin-bottom: 20px;">
                ${entityState?.attributes.logo_url ? html`
                  <img src="${entityState.attributes.logo_url}" 
                       style="width: 120px; height: 120px; object-fit: contain;" />
                ` : html``}
                <div style="display: flex; flex-direction: column;">
                  <div class="source-line">${appName} ${vlmModel ? html`• ${vlmModel} [${confidence}%]` : html``}</div>
                  <h1 class="main-title">${title}</h1>
                  ${series ? html`<h2 class="sub-title">${series}</h2>` : html``}
                </div>
              </div>
            </div>

            <div class="vlm-telemetry-stack">
              
              ${contentType === 'sports' && liveTelemetry ? html`
                <div class="dak-sports-panel">
                  <div class="dak-team-block">
                    <span class="dak-team-name">${liveTelemetry.score?.home?.team}</span>
                    <span class="dak-team-score">${liveTelemetry.score?.home?.score}</span>
                  </div>
                  <div style="color: rgba(255,255,255,0.2); font-weight: 300; font-size: 1.2rem;">|</div>
                  <div class="dak-team-block">
                    <span class="dak-team-name">${liveTelemetry.score?.away?.team}</span>
                    <span class="dak-team-score">${liveTelemetry.score?.away?.score}</span>
                  </div>
                  <div class="dak-sports-meta">
                    <div style="font-weight: 700; color: var(--vlm-accent);">Live</div>
                    <div>P${liveTelemetry.period} • ${liveTelemetry.game_clock}</div>
                  </div>
                </div>
              ` : html``}

              ${sceneAnalysis.mood_tags ? html`
                <div class="dak-tag-row">
                  ${sceneAnalysis.mood_tags.map(tag => html`<span class="dak-badge">${tag}</span>`)}
                </div>
              ` : html``}

              ${sceneAnalysis.visual_description ? html`
                <div class="narrative-placard">
                  <div class="placard-header">AI Scene Telemetry</div>
                  <div class="placard-body">${sceneAnalysis.visual_description}</div>
                </div>
              ` : html``}

            </div>

          </div>
        ` : html`
          <div class="ui-layer dak-standby">
            <div class="dak-standby-title">${currentRoom.name}</div>
            <div class="dak-standby-sub">Standby • Ambient Tracking Active</div>
          </div>
        `}

      </div>
    `;
  }
}

// ============================================================================
// 2. DISCOVERABLE AUTOMATED EDITOR
// ============================================================================
class AiMediaMatrixCardEditor extends LitElement {
  static get properties() {
    return {
      hass: {},
      _config: {},
      _discoveredDevices: { type: Array }
    };
  }

  constructor() {
    super();
    this._discoveredDevices = [];
  }

  setConfig(config) {
    this._config = config;
  }

  updated(changedProperties) {
    if (changedProperties.has('hass') && this.hass) {
      this._discoverIntegrationPlayers();
    }
  }

  _discoverIntegrationPlayers() {
    const allStates = this.hass.states;
    const discovered = [];

    Object.keys(allStates).forEach(entityId => {
      if (entityId.startsWith('media_player.ai_media_')) {
        const stateObj = allStates[entityId];
        let friendlyName = stateObj.attributes.friendly_name || entityId;
        friendlyName = friendlyName.replace('AI Media (', '').replace(')', '');

        discovered.push({
          entity: entityId,
          name: friendlyName,
          icon: stateObj.attributes.icon || 'mdi:television'
        });
      }
    });

    this._discoveredDevices = discovered;
  }

  static get styles() {
    return css`
      .editor-container {
        display: flex;
        flex-direction: column;
        gap: 12px;
        font-family: var(--paper-font-body1_-_font-family, sans-serif);
      }
      .description {
        color: var(--secondary-text-color);
        font-size: 0.9rem;
        margin-bottom: 8px;
      }
      .toggle-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 10px 14px;
        border-radius: 8px;
        border: 1px solid var(--divider-color, #e0e0e0);
        background: var(--card-background-color, #fff);
      }
      .device-info {
        display: flex;
        align-items: center;
        gap: 12px;
      }
      .device-info ha-icon {
        color: var(--primary-color);
      }
      .device-meta {
        display: flex;
        flex-direction: column;
      }
      .device-title {
        font-weight: 600;
        color: var(--primary-text-color);
      }
      .device-id {
        font-size: 0.75rem;
        color: var(--secondary-text-color);
        font-family: monospace;
      }
    `;
  }

  render() {
    if (!this.hass || !this._config) return html``;
    const activeRooms = this._config.rooms || [];

    return html`
      <div class="editor-container">
        <h3>AI Integration Router Engine</h3>
        <p class="description">Toggle active local VLM media proxies into the matrix panel view wrapper:</p>

        ${this._discoveredDevices.map(device => {
          const isEnabled = activeRooms.some(r => r.entity === device.entity);
          return html`
            <div class="toggle-row">
              <div class="device-info">
                <ha-icon icon="${device.icon}"></ha-icon>
                <div class="device-meta">
                  <span class="device-title">${device.name}</span>
                  <span class="device-id">${device.entity}</span>
                </div>
              </div>
              <ha-switch
                .checked="${isEnabled}"
                @change="${(e) => this._toggleDeviceVisibility(device, e.target.checked)}"
              ></ha-switch>
            </div>
          `;
        })}
      </div>
    `;
  }

  _toggleDeviceVisibility(device, shouldEnable) {
    let rooms = JSON.parse(JSON.stringify(this._config.rooms || []));
    if (shouldEnable) {
      if (!rooms.some(r => r.entity === device.entity)) {
        rooms.push({ name: device.name, entity: device.entity, icon: device.icon });
      }
    } else {
      rooms = rooms.filter(r => r.entity !== device.entity);
    }
    this._config = { ...this._config, rooms };
    
    this.dispatchEvent(new CustomEvent('config-changed', { detail: { config: this._config }, bubbles: true, composed: true }));
    this.requestUpdate(); // Explicitly force the editor switches to snap cleanly on click
  }
}

customElements.define('ai-media-matrix-card', AiMediaMatrixCard);
customElements.define('ai-media-matrix-card-editor', AiMediaMatrixCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'ai-media-matrix-card',
  name: 'AI Media Matrix Card',
  description: 'Full-bleed edge-to-edge DAKboard aesthetic for displaying beautiful artwork background with high-contrast text layers.'
});