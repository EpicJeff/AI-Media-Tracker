import {
  LitElement,
  html,
  css
} from 'https://unpkg.com/lit-element@2.4.0/lit-element.js?module';

// ============================================================================
// 1. MAIN CARD ELEMENT (DAKBOARD DESIGN WITH RICH METADATA OVERLAYS)
// ============================================================================
class AiMediaMatrixCard extends LitElement {
  static get properties() {
    return { hass: {}, config: {}, _activeTab: { type: String }, _showCameraOverlay: { type: Boolean }, _refreshInfo: { type: Boolean }, _showCastModal: { type: Boolean }, _showNotifModal: { type: Boolean }, _castSearchText: { type: String }, _castExpandedGroups: { type: Array }, _cameraMuted: { type: Boolean }, _cameraVolume: { type: Number } };
  }

  constructor() {
    super();
    this._cameraMuted = true;
    this._cameraVolume = 0.5;
    this._castExpandedGroups = [];
  }

  static async getConfigElement() {
    return document.createElement('ai-media-matrix-card-editor');
  }

  connectedCallback() {
    super.connectedCallback();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
  }

  setConfig(config) {
    this.config = config || { rooms: [] };
    if (this.config.rooms?.length > 0 && !this._activeTab) {
      this._activeTab = this.config.rooms[0].name;
    }
  }

  shouldUpdate(changedProps) {
    if (changedProps.has('_activeTab')) return true;
    if (changedProps.has('config')) return true;
    if (changedProps.has('_showNotifModal')) return true;
    if (changedProps.has('_showCastModal')) return true;
    if (changedProps.has('_showCameraOverlay')) return true;
    if (changedProps.has('_castSearchText')) return true;
    if (changedProps.has('_castExpandedGroups')) return true;

    if (this._showNotifModal || this._showCastModal || this._showCameraOverlay) return true;

    if (changedProps.has('hass')) {
      const oldHass = changedProps.get('hass');
      const currentRoom = this.config?.rooms?.find(r => r.name === this._activeTab) || this.config?.rooms?.[0];
      if (!currentRoom) return true;

      const oldState = oldHass?.states[currentRoom.entity];
      const newState = this.hass?.states[currentRoom.entity];

      const oldNotif = oldHass?.states['sensor.ai_media_notifications'];
      const newNotif = this.hass?.states['sensor.ai_media_notifications'];

      if (oldState !== newState || oldNotif !== newNotif) {
        return true;
      }
      return false;
    }
    return true;
  }

  static get styles() {
    return css`
      :host { 
        display: block; 
        width: 100%; 
        height: calc(100vh - var(--header-height)); 
        background: #09090a; 
        overflow: hidden; 
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      }
      @keyframes fadeOutTrailer {
        0% { opacity: 0; }
        20% { opacity: 0; }
        30% { opacity: 1; }
        90% { opacity: 1; }
        100% { opacity: 0; }
      }
      .dak-fullscreen-wrapper {
        position: relative;
        width: 100%;
        height: 100%;
        display: flex;
        flex-direction: column;
        padding: 40px 20px;
        box-sizing: border-box;
      }
      .canvas-background {
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background-size: cover;
        background-position: center;
        z-index: 0;
        transition: background-image 0.8s ease;
      }
      .canvas-background.show-fallback {
        background: linear-gradient(-45deg, #23262b, #41404b, #68677e, #35383d); 
        background-size: 400% 400%; 
        animation: gradientBG 15s ease infinite;
      }
      .canvas-overlay {
        position: absolute;
        top: 0;
        left: 0;
        width: 100%; 
        height: 100%;
        background: linear-gradient(0deg, rgba(9, 9, 10, 0.95) 0%, rgba(9, 9, 10, 0.0) 20%, rgba(9, 9, 10, 0.0) 100%); 
        z-index: 1;
      }
      .canvas-overlay-sports {
        position: absolute;
        top: 0;
        left: 0;
        width: 100%; 
        height: 100%;
        background: linear-gradient(0deg, rgba(9, 9, 10, 0.95) 0%, rgba(9, 9, 10, 0.25) 30%, rgba(9, 9, 10, 0.25) 80%, rgba(9, 9, 10, 0.8) 100%)
        z-index: 1;
      }
      .ui-layer {
        position: relative;
        z-index: 2;
      }
      .header-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        width: 100%;
        margin-bottom: 24px;
      }
      .tabs-container {
        display: flex;
        gap: 12px;
      }
      .dak-tab-btn {
        background: rgba(255,255,255,0.05);
        color: rgba(255,255,255,0.5);
        border: 1px solid rgba(255,255,255,0.15);
        padding: 10px 20px;
        border-radius: 30px;
        cursor: pointer;
        text-transform: uppercase;
        font-size: 0.75rem; 
        font-weight: 700;
        letter-spacing: 1px;
        transition: all 0.3s ease;
      }
      .dak-tab-btn.active {
        color: #fff;
        background-color: var(--vlm-accent, #1e5c8a);
        border-color: transparent;
        box-shadow: 0 0 20px rgba(var(--vlm-accent-rgb, 30, 92, 138), 0.4);
      }

      .camera-toggle-btn, .refresh-btn {
        background: transparent; 
        border: none; 
        color: rgba(255,255,255,0.5);
        cursor: pointer; 
        display: flex; 
        align-items: center; 
        gap: 6px; 
        transition: transform 0.6s cubic-bezier(0.68, -0.55, 0.265, 1.55), opacity 0.3s ease, color 0.3s ease;
      }
      .camera-toggle-btn:hover, .refresh-btn:hover {
        color: white;
      }
      .camera-toggle-btn.animating-out, .refresh-btn.animating-out {
        transform: rotate(360deg);
      }
      .camera-toggle-btn.animating-in, .refresh-btn.animating-in {
        pointer-events: none;
      }

      .live-camera-popup {
        position: absolute;
        bottom: 40px;
        right: 40px;
        width: 480px;
        aspect-ratio: 16/9;
        background: #000;
        border-radius: 12px;
        box-shadow: 0 20px 40px rgba(0,0,0,0.8);
        border: 2px solid rgba(255,255,255,0.1);
        z-index: 9999;
        overflow: hidden;
        transform-origin: bottom right;
        transition: transform 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275), opacity 0.4s ease;
      }
      .live-camera-popup.hidden {
        transform: scale(0);
        opacity: 0;
        pointer-events: none;
      }
      .live-camera-close {
        position: absolute;
        top: 8px;
        right: 8px;
        background: rgba(0,0,0,0.5);
        border-radius: 50%;
        padding: 4px;
        color: white;
        cursor: pointer;
        z-index: 10000;
        transition: background 0.2s;
      }
      .live-camera-close:hover {
        background: rgba(255,0,0,0.6);
      }
      
      .live-camera-popup:hover .camera-controls-overlay {
        opacity: 1 !important;
      }

      .camera-toggle-icon {
        --mdc-icon-size: 48px;
        opacity:1.0
      }

      .refresh-icon {
        --mdc-icon-size: 48px;
        opacity:1.0
      }

      .athlete-headshot {
        object-fit: contain;
        background-size:100%;
        background-repeat:no-repeat;
        background-position:bottom;
        width: 40vw;
        height:40vw;
        max-width: 800px;
        max-height: 800px;
        position: absolute;
        bottom: calc(50% - min(10vw, 200px));
        filter: drop-shadow(0 0 30px rgba(0, 0, 0, 0.9));position: absolute;
        -webkit-mask-image: linear-gradient(to bottom, rgba(0, 0, 0, 1) 80%, rgba(0, 0, 0, 0) 100%);
        mask-image: linear-gradient(to bottom, rgba(0, 0, 0, 1) 80%, rgba(0, 0, 0, 0) 100%);
      }

      .athlete-logo {
        background-image: url('/local/ai_media_cache/generic_athlete.png');
        object-fit: contain;
        background-size:100%;
        background-repeat:no-repeat;
        background-position:bottom;
        width: 40vw;
        height:40vw;
        max-width: 800px;
        max-height: 800px;
        /*filter: drop-shadow(0 0 30px rgba(0, 0, 0, 0.9));*/
        position: absolute;
        bottom: calc(50% - min(10vw, 200px));
        justify-content: center;
        align-items: flex-end;
        display: flex;
      }

      .athlete-name {
        font-size: 3vw;
        font-weight: 800;
        color: #fff;
        width: 40vw;
        height:40vw;
        max-width: 800px;
        max-height: 800px;
        /*filter: drop-shadow(0 0 30px rgba(0, 0, 0, 0.9));*/
        position: absolute;
        bottom: calc(50% - min(10vw, 200px));
        justify-content: center;
        align-items: flex-end;
        display: flex;
      }

      .vs-logo {
        position: absolute;
        bottom: calc(50% - min(10vw, 200px));
      }

      /* Display Grid Zone */
      .bottom-display-zone {
        display: grid;
        grid-template-columns: 1.2fr 0.8fr;
        align-items: flex-end;
        gap: 20px;
        width: 100%;
        margin-top: auto;
      }

      .media-meta-stack {
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      .meta-header-row {
        display: flex;
        align-items: center;
        gap: 20px;
        margin-bottom: 10px;
      }
      .channel-logo {
        width: 100px;
        height: 100px;
        object-fit: contain;
        filter: drop-shadow(0 4px 8px rgba(0,0,0,0.5));
        filter: saturate(0%);
      }
      .app-badge {
        align-self: flex-start;
        background: rgba(255, 255, 255, 0.1);
        border: 1px solid rgba(255, 255, 255, 0.2);
        padding: 4px 12px;
        border-radius: 12px;
        font-size: 0.8rem;
        font-weight: 600;
        color: rgba(255, 255, 255, 0.8);
        text-transform: uppercase;
        letter-spacing: 0.5px;
      }
      .content-rating {
        border:1px solid rgba(255,255,255,0.4);
        padding:2px 6px;
        margin: 14px 0px 0px 0px;
        border-radius:4px;
        font-size:1.4rem;
        font-weight:bold;
        color:rgba(255,255,255,0.7);
      }
      .main-title {
        font-size: 3.8rem;
        font-weight: 800;
        line-height: 1.1;
        color: #fff;
        margin: 0;
        text-shadow: 0 4px 12px rgba(0,0,0,0.5);
      }
      .sub-title {
        font-size: 2rem;
        font-weight: 400;
        line-height: 1.1;
        color: #aaa;
        margin: 0;
        text-shadow: 0 4px 12px rgba(0,0,0,0.5);
      }

      /* AI Telemetry / Scene Analysis */
      .telemetry-placard {
        margin-top: 24px;
        background: rgba(255,255,255,0.05);
        border: 1px solid rgba(255,255,255,0.1);
        border-radius: 16px;
        padding: 16px;
      }
      .telemetry-header {
        font-size: 0.7rem;
        font-weight: 800;
        text-transform: uppercase;
        color: var(--vlm-accent, #1e5c8a);
        letter-spacing: 1.5px;
        margin-bottom: 8px;
      }
      .telemetry-body {
        font-size: 0.9rem;
        color: rgba(255,255,255,0.8);
        font-style: italic;
        line-height: 1.4;
      }
      .dak-badge {
        font-size: 0.65rem;
        font-weight: 700;
        background: rgba(255,255,255,0.15);
        padding: 3px 8px;
        border-radius: 4px;
        margin-right: 6px;
        text-transform: uppercase;
      }
      /* Movie/Show TMDB Information Card */
      .show-details-card {
        background: rgba(18, 18, 20, 0.65);
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 24px;
        padding: 30px;
        display: flex;
        flex-direction: column;
        gap: 16px;
        color: #fff;
        box-shadow: 0 10px 30px rgba(0,0,0,0.3);
      }
      .show-meta-badges {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        align-items: center;
      }
      .rating-badge {
        background: #f5c518;
        color: #000;
        font-weight: 800;
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 0.8rem;
      }
      .genre-badge {
        background: rgba(255,255,255,0.1);
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 0.8rem;
        border: 1px solid rgba(255,255,255,0.1);
      }
      .duration-badge {
        font-size: 0.8rem;
        color: rgba(255,255,255,0.6);
        margin-left: auto;
      }
      .show-overview {
        font-size: 0.95rem;
        line-height: 1.5;
        color: rgba(255, 255, 255, 0.7);
        margin: 0;
        display: -webkit-box;
        -webkit-line-clamp: 4;
        -webkit-box-orient: vertical;
        overflow: hidden;
      }

      /* Live Sports Scoreboards */
      .sports-details-card {
        background: rgba(18, 18, 20, 0.75);
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        border: 1px solid rgba(255, 255, 255, 0.15);
        border-radius: 24px;
        padding: 30px;
        color: #fff;
        box-shadow: 0 10px 30px rgba(0,0,0,0.4);
      }
      .sports-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-bottom: 1px solid rgba(255,255,255,0.1);
        padding-bottom: 12px;
        margin-bottom: 20px;
      }
      .game-status {
        font-size: 0.85rem;
        text-transform: uppercase;
        font-weight: 700;
        letter-spacing: 1px;
        color: #ff3b30;
        display: flex;
        align-items: center;
        gap: 6px;
      }
      .game-status.live::before {
        content: '';
        display: inline-block;
        width: 8px;
        height: 8px;
        background: #ff3b30;
        border-radius: 50%;
        animation: blink 1s infinite alternate;
      }
      .game-time {
        font-size: 0.9rem;
        color: rgba(255,255,255,0.6);
        font-weight: 600;
      }

      /* Tennis Court Styling */
      .tennis-court {
        position: relative;
        background: linear-gradient(135deg, #1e5c8a 0%, #154263 100%);
        border: 3px solid #fff;
        border-radius: 16px;
        height: 220px;
        margin-top: 16px;
        overflow: hidden;
        box-shadow: inset 0 0 30px rgba(0,0,0,0.4), 0 8px 24px rgba(0,0,0,0.2);
      }
      .tennis-court::before {
        content: '';
        position: absolute;
        top: 0;
        bottom: 0;
        left: 50%;
        width: 2px;
        background: rgba(255, 255, 255, 0.9);
        z-index: 1;
        box-shadow: 0 0 4px rgba(0,0,0,0.2);
      }
      .tennis-court-inner {
        position: absolute;
        top: 16px;
        bottom: 16px;
        left: 0;
        right: 0;
        border-top: 2px solid rgba(255,255,255,0.7);
        border-bottom: 2px solid rgba(255,255,255,0.7);
        pointer-events: none;
      }
      .tennis-service-boxes {
        position: absolute;
        top: 16px;
        bottom: 16px;
        left: 20%;
        right: 20%;
        border-left: 2px solid rgba(255,255,255,0.7);
        border-right: 2px solid rgba(255,255,255,0.7);
        pointer-events: none;
      }
      .tennis-service-line {
        position: absolute;
        top: 50%;
        left: 20%;
        right: 20%;
        height: 2px;
        background: rgba(255,255,255,0.7);
        transform: translateY(-50%);
        pointer-events: none;
      }
      .tennis-player-spot {
        position: absolute;
        top: 50%;
        transform: translateY(-50%);
        display: flex;
        flex-direction: column;
        align-items: center;
        z-index: 2;
        width: 130px;
        text-align: center;
      }
      .tennis-player-spot.away {
        left: 5%;
      }
      .tennis-player-spot.home {
        right: 5%;
      }
      .tennis-court-photo {
        width: 72px;
        height: 72px;
        border-radius: 50%;
        border: 3px solid #fff;
        background: rgba(9,9,10,0.6);
        object-fit: cover;
        box-shadow: 0 6px 12px rgba(0,0,0,0.4);
      }
      .tennis-court-name {
        font-size: 0.85rem;
        font-weight: 700;
        color: #fff;
        margin-top: 8px;
        text-shadow: 0 2px 4px rgba(0,0,0,0.8);
        background: rgba(9,9,10,0.65);
        padding: 4px 10px;
        border-radius: 20px;
        display: flex;
        align-items: center;
        gap: 6px;
        max-width: 120px;
        justify-content: center;
        border: 1px solid rgba(255,255,255,0.1);
      }

      /* Golf Leaderboard Styling */
      .golf-card {
        background: linear-gradient(135deg, #133a1b 0%, #0d2812 100%);
        border: 1px solid rgba(255,255,255,0.15);
        border-radius: 24px;
        padding: 30px;
        color: #fff;
        box-shadow: 0 10px 30px rgba(0,0,0,0.4);
        position: relative;
        overflow: hidden;
      }
      .golf-card::after {
        content: '';
        position: absolute;
        bottom: -40px;
        right: -40px;
        width: 180px;
        height: 180px;
        background: radial-gradient(circle, rgba(255,255,255,0.04) 0%, rgba(255,255,255,0) 70%);
        border-radius: 50%;
        pointer-events: none;
      }
      .golf-leader-row {
        display: flex;
        align-items: center;
        gap: 20px;
        background: rgba(255,255,255,0.06);
        padding: 16px;
        border-radius: 16px;
        border: 1px solid rgba(255,255,255,0.1);
        margin-top: 12px;
      }
      .golf-leader-photo {
        width: 76px;
        height: 76px;
        border-radius: 50%;
        border: 3px solid #f5c518;
        background: rgba(9,9,10,0.4);
        object-fit: cover;
        box-shadow: 0 4px 10px rgba(0,0,0,0.3);
      }
      .golf-leader-info {
        display: flex;
        flex-direction: column;
        flex-grow: 1;
      }
      .golf-leader-label {
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 1px;
        color: #f5c518;
        text-transform: uppercase;
      }
      .golf-leader-name {
        font-size: 1.3rem;
        font-weight: 800;
        margin-top: 2px;
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .golf-leader-score {
        font-size: 2.4rem;
        font-weight: 900;
        font-family: monospace;
        color: #2ecc71;
        text-shadow: 0 2px 4px rgba(0,0,0,0.3);
      }

      /* Team Row Construction */
      .teams-container {
        display: flex;
        flex-direction: column;
        gap: 0px;
      }
      .team-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
      }
      .team-identity {
        display: flex;
        align-items: center;
        gap: 16px;
        padding-top: 20px;
      }
      .team-logo {
        width: 48px;
        height: 48px;
        object-fit: contain;
      }
      .tennis-athlete-logo {
        width: 54px;
        height: 54px;
        border-radius: 50%;
        border: 2px solid rgba(255,255,255,0.2);
        background: rgba(255,255,255,0.05);
        object-fit: cover;
      }
      .flag-icon {
        width: 20px;
        height: 14px;
        border-radius: 2px;
        object-fit: cover;
        box-shadow: 0 1px 3px rgba(0,0,0,0.3);
      }
      .team-name {
        font-size: 1.4rem;
        font-weight: 700;
      }
      .team-seed {
        font-size: 0.8rem;
        color: rgba(255,255,255,0.5);
        background: rgba(255,255,255,0.1);
        padding: 2px 6px;
        border-radius: 4px;
        margin-left: 4px;
      }
      
      /* Scores Grid */
      .scores-display {
        display: flex;
        align-items: center;
        gap: 16px;
      }
      .score-column {
        display:flex; 
        flex-direction:column; 
        align-items:center;
        width: 40px
      }
      .tennis-set-scores {
        display: flex;
        gap: 8px;
        color: rgba(255,255,255,0.4);
        font-size: 1.1rem;
        font-weight: 600;
      }
      .tennis-set-scores span.active-set {
        color: #fff;
        font-weight: 700;
      }
      .primary-score {
        font-size: 2.2rem;
        font-weight: 800;
        font-family: monospace;
        width: 60px;
      }

      @keyframes blink {
        from { opacity: 0.3; }
        to { opacity: 1; }
      }
      @keyframes pulse-glow {
        0% { box-shadow: 0 0 10px var(--vlm-accent, #1e5c8a); }
        50% { box-shadow: 0 0 20px var(--vlm-accent, #1e5c8a), 0 0 40px var(--vlm-accent, #1e5c8a); }
        100% { box-shadow: 0 0 10px var(--vlm-accent, #1e5c8a); }
      }
      @keyframes fadeIn {
        from { opacity: 0; }
        to { opacity: 1; }
      }
      @keyframes gradientBG {
        0% { background-position: 0% 50%; }
        50% { background-position: 100% 50%; }
        100% { background-position: 0% 50%; }
      }
      @media (max-width: 1280px) {
        .main-title {
          font-size: 2.8rem;
        }
        .sub-title {
          font-size: 1.5rem;
        }
      }

      @media (max-aspect-ratio: 9/16) {
        .athlete-logo, .athlete-headshot, .athlete-name, .vs-logo {
          bottom:60%;
        }
      }

      @media (max-width: 900px) {
        .bottom-display-zone {
          grid-template-columns: 1fr;
          gap: 30px;
        }
        .main-title {
          font-size: 2.8rem;
        }
        .sub-title {
          font-size: 1.5rem;
        }
        .channel-logo {
          width: 70px;
          height: 70px;
        }
        .sports-info-panel, .show-details-card {
           transform: scale(0.9);
           transform-origin: left bottom;
        }
      }

      @media (max-width: 600px) {
        .main-title {
          font-size: 2.0rem;
        }
        .sub-title {
          font-size: 1.2rem;
        }
        .channel-logo {
          width: 50px;
          height: 50px;
        }
        .sports-info-panel, .show-details-card {
           transform: scale(0.75);
           transform-origin: left bottom;
           width: 133%; /* compensate for 0.75 scaling */
        }
      }
    `;
  }

  // Safe accessor helper to prevent "list index out of range"
  _safeGet(obj, path, fallback) {
    return path.split('.').reduce((acc, part) => acc && acc[part], obj) ?? fallback;
  }

  _renderCanvasBackground(isPlaying, poster, sports, logoUrl, seriesTitle, title, appName, cameraUrl, cameraEntityId = null, trailerId = null, isContentBlocked = false, violationTag = '') {
    if (isContentBlocked) {
      return html`
        <div class="canvas-background" style="background-image: url('${poster || ''}'); filter: blur(20px) grayscale(80%); transform: scale(1.1);"></div>
        <div class="canvas-overlay"></div>
        <div style="position:absolute; top:0; left:0; width:100%; height:100%; display:flex; flex-direction:column; justify-content:center; align-items:center; z-index:5; pointer-events:none;">
          <ha-icon icon="mdi:shield-lock" style="--mdc-icon-size: 100px; color: #e74c3c; filter: drop-shadow(0 0 20px rgba(231,76,60,0.5));"></ha-icon>
          <div style="font-size: 3rem; font-weight: 900; color: #fff; margin-top: 20px; text-transform: uppercase; letter-spacing: 2px; text-shadow: 0 4px 10px rgba(0,0,0,0.8);">Restricted Content</div>
          <div style="background: #e74c3c; color: #fff; padding: 8px 16px; border-radius: 20px; font-weight: bold; font-size: 1.2rem; margin-top: 15px; text-transform: uppercase; box-shadow: 0 4px 15px rgba(231,76,60,0.4);">Blocked: ${violationTag}</div>
        </div>
      `;
    }

    if (!isPlaying) {
      return html`
        <div class="canvas-background" style="background-color: #0c0c0d;">
          <div style="font-size:5vw;position:absolute;top:calc(50% - 2vw);width:100%;text-align:center;color:#fff;">Standby</div>
        </div>
        <div class="canvas-overlay"></div>`;
    }

    if(seriesTitle == "Browsing..." || title == "Browsing...") {
      return html`
        <div class="canvas-background">
          <div style="position:absolute; top:0; left:0; width:100%; height:100%; background-image: url('/local/ai_media_cache/backgrounds/browsing.jpg'); background-size: cover; background-position: center; pointer-events:none; z-index:0;"></div>
        </div>
        <div class="canvas-overlay" style="z-index:1;"></div>
      `;
    }

    if (poster && (!sports || !sports.away_team)) {
      return html`
        <div class="canvas-background">
          ${poster ? html`<div style="position:absolute; top:0; left:0; width:100%; height:100%; background-image:url(${poster}); background-size:cover; background-position:center; filter:blur(0px); opacity:1.0;"></div>` : html``}
          ${trailerId ? html`
            <iframe 
              src="https://www.youtube-nocookie.com/embed/${trailerId}?autoplay=1&mute=1&controls=0&modestbranding=1&rel=0&showinfo=0&playsinline=1&disablekb=1&iv_load_policy=3" 
              style="position:absolute; top:-20%; left:-20%; width:140%; height:140%; border:none; pointer-events:none; animation: fadeOutTrailer 30s forwards; transform: scale(1.1);"
              allow="autoplay; encrypted-media"
              referrerpolicy="strict-origin-when-cross-origin">
            </iframe>
          ` : html``}
          <div style="position:absolute; top:0; left:0; width:100%; height:100%; background:linear-gradient(0deg, rgba(9,9,10,1) 0%, rgba(9,9,10,0.4) 20%, rgba(9,9,10,0.8) 100%);"></div>
        </div>
        <div class="canvas-overlay"></div>
      `;
    }
    
    // If it's a team sport, dynamically generate the split background with logos
    if (sports && sports.is_individual === false && sports.away_color && sports.home_color) {
      const bgUrl = sports.background_url || poster || '';
      return html`
        <div class="canvas-background" style="background-color: #111;">
          
          <!-- Base layer: The stadium or generic background -->
          ${bgUrl ? html`<div style="position:absolute; top:0; left:0; width:100%; height:100%; background-image:url('${bgUrl}'); background-size:cover; background-position:center; filter:blur(4px); opacity:0.3;"></div>` : html``}
          
          <!-- Diagonal color split overlay using multiply blend mode for rich colors -->
          <div style="position:absolute; top:0; left:0; width:100%; height:100%; background:linear-gradient(90deg, ${sports.away_color} 0%, ${sports.away_color} 50%, ${sports.home_color} 50%, ${sports.home_color} 100%); mix-blend-mode:color; opacity:1.0;"></div>
          
          <!-- Huge ghosted logos in the background -->
          <div style="position:absolute; top:0; left:0; width:100%; height:100%; display:flex; justify-content:space-between; align-items:center; padding:0;">
            ${sports.away_logo ? html`<img src="${sports.away_logo}" style="width:30vw; max-width:800px; opacity:1.0; filter: drop-shadow(0 -4px 0 rgba(255,255,255,1)) drop-shadow(-4px 0 0 rgba(255,255,255,1)) drop-shadow(4px 0 0 rgba(255,255,255,1)) drop-shadow(0 4px 0 rgba(255,255,255,1)) drop-shadow(0 0 30px rgba(0,0,0,1)); transform:translate(20%, 0%);">` : html`<div></div>`}
            ${sports.home_logo ? html`<img src="${sports.home_logo}" style="width:30vw; max-width:800px; opacity:1.0; filter: drop-shadow(0 -4px 0 rgba(255,255,255,1)) drop-shadow(-4px 0 0 rgba(255,255,255,1)) drop-shadow(4px 0 0 rgba(255,255,255,1)) drop-shadow(0 4px 0 rgba(255,255,255,1)) drop-shadow(0 0 30px rgba(0,0,0,1)); transform:translate(-20%, 0%);">` : html`<div></div>`}
          </div>          
          
          <!-- VS Diamond -->
          <div style="position:absolute; top:50%; left:50%; transform:translate(-50%, -50%); z-index:0;">
            <div style="width:10vw; height:10vw; max-width: 30vw; max-height: 30vw; background:#111; border:8px solid rgba(255,255,255,0.8); border-radius: 24px; transform:rotate(45deg); box-shadow:0 10px 30px rgba(0,0,0,0.8); display:flex; justify-content:center; align-items:center;">
              <span style="transform:rotate(-45deg); font-size:5vw; font-weight:900; color:#fff; font-style:italic;">VS</span>
            </div>
          </div>
          
        </div>
        <div class="canvas-overlay-sports"></div>
      `;
    }

    // Golf sport
    if (sports && sports.is_individual === true && sports.sport == "golf") {
      const bgUrl = sports.background_url || poster || '';
      return html`
        <div class="canvas-background" style="background-color: #111;">
          ${bgUrl ? html`<div style="position:absolute; top:0; left:0; width:100%; height:100%; background-image:url('${bgUrl}'); background-size:cover; background-position:center; filter:blur(8px); opacity:0.5;"></div>` : html``}
          <div style="position:absolute; top:0; left:0; width:100%; height:100%; display:flex; justify-content:flex-start; align-items:flex-end; padding:0 10%;">
            ${sports.away_logo ? html`<img src="${sports.away_logo}" style="height:70vh; max-height:800px; filter:drop-shadow(0 0 30px rgba(0,0,0,0.9)); object-fit:contain; object-position:bottom; transform:translate(20%, 0%);">` : html`<div></div>`}
          </div>
        </div>
        <div class="canvas-overlay-sports"></div>
      `;
    }

    // Individual sport (e.g. Tennis)
    if (sports && sports.is_individual === true) {
      const bgUrl = sports.background_url || poster || '';
      return html`
        <div class="canvas-background" style="background-color: #111;">
          ${bgUrl ? html`<div style="position:absolute; top:0; left:0; width:100%; height:100%; background-image:url('${bgUrl}'); background-size:cover; background-position:center; filter:blur(8px); opacity:0.5;"></div>` : html``}
          <div style="position:absolute; top:0; left:0; width:100%; height:100%; display:flex; justify-content:flex-start; align-items:flex-end; padding:0 10%;">
            ${sports.away_logo ? html`<div class="athlete-headshot" style="background-image: url('${sports.away_logo}'); left: -5vw;"></div><div class="athlete-name" style="left: -5vw;"><span>${sports.away_team}</span></div>` : html`<div class="athlete-logo" style="left: -5vw;"></div><div class="athlete-name" style="left: -5vw;"><span>${sports.away_team}</span></div>`}
            ${sports.home_logo ? html`<div class="athlete-headshot" style="background-image: url('${sports.home_logo}'); right: 14vw;"></div><div class="athlete-name" style="right: 14vw;"><span>${sports.home_team}</span></div>` : html`<div class="athlete-logo" style="right: 14vw;"></div><div class="athlete-name" style="right: 14vw;"><span>${sports.home_team}</span></div>`}
          </div>                
          
          <!-- VS Diamond -->
          <div class="vs-logo" style="left:50%; transform:translate(-50%, -50%); z-index:0;">
            <div style="width:10vw; height:10vw; max-width: 30vw; max-height: 30vw; background:rgba(32, 32, 32, 0.75); border:8px solid rgba(255,255,255,0.8); border-radius: 24px; transform:rotate(45deg); box-shadow:0 10px 30px rgba(0,0,0,0.8); display:flex; justify-content:center; align-items:center;">
              <span style="transform:rotate(-45deg); font-size:5vw; font-weight:900; color:#fff; font-style:italic;">VS</span>
            </div>
          </div>

        </div>
        <div class="canvas-overlay-sports"></div>
      `;
    }
    
    // Standard poster background (e.g. TMDB)
    if (poster) {
      return html`<div class="canvas-background" style="background-image: url('${poster}');"></div>
        <div class="canvas-overlay"></div>
        `;
    }

    if(logoUrl) {
      return html`
        <div class="canvas-background show-fallback">
            <div style="position:absolute; top:0; left:0; width:100%; height:100%; display:flex; justify-content:center; align-items:center;">
              <img src="${logoUrl}" style="width:100vw; max-width:1280px; opacity:0.25; filter:blur(4px) drop-shadow(0 0 50px rgba(255,255,255,0.2));">
            </div>
        </div>
        <div class="canvas-overlay"></div>
      `;
    }

    if(seriesTitle && title) {return html`
      <div class="canvas-background show-fallback">
          <div style="font-size:5vw;position:absolute;top:calc(50% - 2vw);width:100%;text-align:center;color:#fff;">${seriesTitle}</div>
          ${title ? html`<div style="font-size:3.5vw;position:absolute;top:calc(50% + 3vw);width:100%;text-align:center;color:#aaa;">${title}</div>` : html``}
      </div>
      <div class="canvas-overlay"></div>
      `;
    }

    if(title) {return html`
      <div class="canvas-background show-fallback">
          <div style="font-size:5vw;position:absolute;top:calc(50% - 2vw);width:100%;text-align:center;color:#fff;">${title}</div>
      </div>
      <div class="canvas-overlay"></div>
      `;
    }
    
    // Complete Text Fallback (Dynamic Gradient)
    return html`
      <div class="canvas-background">
      </div>
      <div class="canvas-overlay"></div>
    `;
  }

  _refreshAnalysis(entityId) {
    if (!this.hass) return;
    this._refreshInfo = true;
    this.hass.callService("ai_media_tracker", "refresh_analysis", {
      entity_id: entityId
    });
    setTimeout(() => {
      this._refreshInfo = false;
    }, 1000);
  }

  _updateCameraVolume() {
    setTimeout(() => {
      const stream = this.shadowRoot.querySelector('ha-camera-stream');
      if (!stream) return;
      const player = stream.shadowRoot.querySelector('ha-hls-player') || stream.shadowRoot.querySelector('ha-web-rtc-player');
      if (player) {
        const video = player.shadowRoot.querySelector('video');
        if (video) {
          video.volume = this._cameraVolume;
          video.muted = this._cameraMuted;
        }
      }
    }, 50);
  }

  _castStream(sourceEntity, targetReceiver) {
    if (!this.hass) return;
    this.hass.callService("ai_media_tracker", "follow_me", {
      entity_id: sourceEntity,
      target_receivers: targetReceiver
    });
  }

  _stopCast(sourceEntity, targetReceiver) {
    if (!this.hass) return;
    this.hass.callService("ai_media_tracker", "stop_cast", {
      entity_id: sourceEntity,
      target_receivers: targetReceiver
    });
  }

  _stopAllCasts(sourceEntity, targetReceivers) {
    this.hass.callService("ai_media_tracker", "stop_cast", {
      entity_id: sourceEntity,
      target_receivers: targetReceivers
    });
  }

  _toggleExpandedGroup(entityId) {
    if (this._castExpandedGroups.includes(entityId)) {
      this._castExpandedGroups = this._castExpandedGroups.filter(id => id !== entityId);
    } else {
      this._castExpandedGroups = [...this._castExpandedGroups, entityId];
    }
  }

  _toggleGroupTarget(parentEntityId, childEntityId, isCurrentlyChecked) {
    if (isCurrentlyChecked) {
      this.hass.callService("ai_media_tracker", "ungroup_targets", {
        child_entity_ids: [childEntityId]
      });
    } else {
      this.hass.callService("ai_media_tracker", "group_targets", {
        parent_entity_id: parentEntityId,
        child_entity_ids: [childEntityId]
      });
    }
  }

  render() {
    if (!this.hass || !this.config) return html``;
    const rooms = this.config.rooms || [];
    let currentRoom = rooms.find(r => r.name === this._activeTab) || rooms[0];
    
    if (!currentRoom) return html`<div style="color:#fff; padding:50px;">Assign channels in config.</div>`;

    const state = this.hass.states[currentRoom.entity];
    const isPlaying = state && state.state !== 'off' && state.state !== 'unavailable';
    const poster = state?.attributes.entity_picture;
    const logoUrl = state?.attributes.logo_url;

    if (logoUrl) {
      this.style.setProperty('--vlm-accent', '#1e5c8a'); 
    }

    // Extract attributes
    const sportsData = state?.attributes.sports_data;
    const showInfo = state?.attributes.show_info;
    const sceneAnalysis = state?.attributes.scene_analysis || {};
    const appName = state?.attributes.network_name || state?.attributes.channel_platform_name || state?.attributes.app_name || state?.attributes.source;
    const seriesTitle = state?.attributes.media_series_title || null;
    const title = state?.attributes.media_title || null;
    const contentRating = showInfo?.content_rating || null;
    const vlmModel = state?.attributes.vlm_model;
    const confidence = state?.attributes.confidence ? Math.round(state.attributes.confidence * 100) : null;
    const trailerId = showInfo?.trailer_youtube_id || null;
    const cameraUrl = state?.attributes.camera_stream_url;
    const cameraEntityId = state?.attributes.camera_entity_id;
    const isContentBlocked = state?.attributes.is_content_blocked || false;
    const violationTag = state?.attributes.content_violation_tag || '';
    
    const notifState = this.hass.states['sensor.ai_media_tracker_notifications'];
    const unreadCount = notifState ? parseInt(notifState.state) || 0 : 0;
    const notifications = notifState?.attributes?.notifications || [];

    var displayTitle = title;
    if(sportsData.away_team && sportsData.home_team && sportsData.is_individual === false)
    {
      displayTitle = sportsData.away_team + " vs " + sportsData.home_team;
    }
    return html`
      ${this._renderCanvasBackground(isPlaying, poster, sportsData, logoUrl, seriesTitle, title, appName, cameraUrl, cameraEntityId, trailerId, isContentBlocked, violationTag)}
      
      <div class="dak-fullscreen-wrapper">
        <div class="header-row ui-layer" style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
          <div class="tabs-container">
            ${rooms.map(r => html`
              <button class="dak-tab-btn ${this._activeTab === r.name ? 'active' : ''}" @click="${() => this._activeTab = r.name}">
                ${r.name}
              </button>
            `)}
          </div>
          <div style="display:flex; align-items:center; gap:12px;">
            ${cameraEntityId ? html`
              <button class="camera-toggle-btn ${this._showCameraOverlay ? 'animating-out' : ''}" @click="${() => this._showCameraOverlay = true}">
                <ha-icon icon="mdi:cctv" class="camera-toggle-icon"></ha-icon>
              </button>
            ` : html``}
            
            <div style="position:relative;">
              <button class="refresh-btn" @click="${() => { this._showNotifModal = !this._showNotifModal; if(this._showNotifModal && unreadCount > 0) this.hass.callService('ai_media_tracker', 'mark_notifications_read'); }}" title="Notifications">
                <ha-icon icon="mdi:bell" class="refresh-icon" style="${unreadCount > 0 ? 'color:#e74c3c;' : ''}"></ha-icon>
                ${unreadCount > 0 ? html`
                  <div style="position:absolute; top:-6px; right:-6px; background:#e74c3c; color:#fff; font-size:11px; font-weight:bold; border-radius:12px; padding:2px 6px; box-shadow: 0 0 5px rgba(231, 76, 60, 0.5); pointer-events:none;">${unreadCount}</div>
                ` : html``}
              </button>

              ${this._showNotifModal ? html`
                <div style="position:fixed; top:0; left:0; width:100%; height:100%; z-index:10000;" @click="${(e) => { e.stopPropagation(); this._showNotifModal = false; }}"></div>
                <div style="position:absolute; top:45px; right:0; background:rgba(30,30,30,0.95); backdrop-filter:blur(10px); border-radius:12px; width:350px; border:1px solid rgba(255,255,255,0.1); z-index:10001; box-shadow: 0 10px 30px rgba(0,0,0,0.5); display:flex; flex-direction:column; overflow:hidden;">
                  <div style="padding:16px; border-bottom:1px solid rgba(255,255,255,0.05); display:flex; justify-content:space-between; align-items:center;">
                    <div style="display:flex; flex-direction:column;">
                       <span style="color:#fff; font-weight:bold; font-size:1rem;">Notifications</span>
                    </div>
                    <button style="background:rgba(255, 255, 255, 0.1); color:#fff; border:none; border-radius:4px; padding:4px 8px; font-size:0.75rem; cursor:pointer; transition:background 0.2s;" @click="${() => this.hass.callService('ai_media_tracker', 'clear_notification')}">Clear All</button>
                  </div>
                  
                  <div style="max-height:400px; overflow-y:auto; padding:8px; display:flex; flex-direction:column; gap:8px;">
                    ${notifications.length === 0 ? html`<div style="padding:20px; text-align:center; color:#888;">No notifications</div>` : 
                      notifications.map(n => html`
                        <div style="background:rgba(255,255,255,0.05); border-radius:8px; padding:12px; position:relative; display:flex; flex-direction:column; gap:8px;">
                          <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                            <div style="display:flex; align-items:center; gap:6px; padding-right:8px;">
                              ${n.type === 'violation' ? html`<ha-icon icon="mdi:shield-alert" style="color:#e74c3c; --mdc-icon-size:16px;"></ha-icon>` : html`<ha-icon icon="mdi:information" style="color:#3498db; --mdc-icon-size:16px;"></ha-icon>`}
                              <span style="color:#fff; font-weight:bold; font-size:0.9rem;">${n.title}</span>
                            </div>
                            <div style="display:flex; align-items:center; gap:8px;">
                              <span style="color:#888; font-size:0.7rem;">${new Date(n.timestamp).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}</span>
                              <button style="background:none; border:none; color:#888; cursor:pointer; padding:0; display:flex;" @click="${() => this.hass.callService('ai_media_tracker', 'clear_notification', { notification_id: n.id })}">
                                <ha-icon icon="mdi:close" style="--mdc-icon-size:14px;"></ha-icon>
                              </button>
                            </div>
                          </div>
                          <span style="color:#bbb; font-size:0.85rem; line-height:1.3;">${n.message}</span>
                          ${n.image_url ? html`<img src="${n.image_url}" style="width:100%; height:auto; border-radius:6px; margin-top:4px; border:1px solid rgba(255,255,255,0.1); object-fit:cover;" />` : html``}
                        </div>
                      `)
                    }
                  </div>
                </div>
              ` : html``}
            </div>
            
            <div style="position:relative;">
              <button class="refresh-btn" @click="${() => this._showCastModal = !this._showCastModal}" title="Follow Me / Cast">
                <ha-icon icon="${state?.attributes?.active_casts && state.attributes.active_casts.length > 0 ? 'mdi:cast-connected' : 'mdi:cast'}" class="refresh-icon" style="${state?.attributes?.active_casts && state.attributes.active_casts.length > 0 ? 'color:#2ecc71;' : ''}"></ha-icon>
                ${state?.attributes?.active_casts && state.attributes.active_casts.length > 0 ? html`
                  <div style="position:absolute; top:-6px; right:-6px; background:#2ecc71; color:#111; font-size:11px; font-weight:bold; border-radius:12px; padding:2px 6px; box-shadow: 0 0 5px rgba(46, 204, 113, 0.5); pointer-events:none;">${state.attributes.active_casts.length}</div>
                ` : html``}
              </button>

              ${this._showCastModal ? html`
                <div style="position:fixed; top:0; left:0; width:100%; height:100%; z-index:10000;" @click="${(e) => { e.stopPropagation(); this._showCastModal = false; }}"></div>
                <div style="position:absolute; top:45px; right:0; background:rgba(30,30,30,0.95); backdrop-filter:blur(10px); border-radius:12px; width:300px; border:1px solid rgba(255,255,255,0.1); z-index:10001; box-shadow: 0 10px 30px rgba(0,0,0,0.5); display:flex; flex-direction:column; overflow:hidden;">
                  <div style="padding:16px; border-bottom:1px solid rgba(255,255,255,0.05); display:flex; flex-direction:column; gap:12px;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                      <div style="display:flex; flex-direction:column;">
                         <span style="color:#fff; font-weight:bold; font-size:1rem;">Cast Group</span>
                         <span style="color:#aaa; font-size:0.8rem;">Select screens to sync</span>
                      </div>
                      ${state?.attributes?.active_casts && state.attributes.active_casts.length > 0 ? html`
                        <button style="background:rgba(231, 76, 60, 0.2); color:#e74c3c; border:1px solid #e74c3c; border-radius:4px; padding:4px 8px; font-size:0.75rem; cursor:pointer;" @click="${() => this._stopAllCasts(currentRoom.entity, state.attributes.active_casts)}">Stop All</button>
                      ` : html``}
                    </div>
                    <input type="text" placeholder="Search devices..." .value="${this._castSearchText || ''}" @input="${(e) => this._castSearchText = e.target.value}" style="width:100%; box-sizing:border-box; padding:8px 12px; border-radius:6px; border:1px solid rgba(255,255,255,0.1); background:rgba(0,0,0,0.3); color:#fff; font-family:inherit; font-size:0.9rem;">
                  </div>
                  
                  <div style="max-height:350px; overflow-y:auto; padding:8px; display:flex; flex-direction:column; gap:4px;">
                    ${(() => {
                      const groups = state?.attributes?.target_groups || {};
                      const allowedTargets = state?.attributes?.cast_targets || [];
                      
                      const allDevices = Object.values(this.hass.states).filter(s => {
                        if (!s.entity_id.startsWith('media_player.')) return false;
                        if (s.entity_id.startsWith('media_player.ai_media_')) return false;
                        if (allowedTargets.length > 0 && !allowedTargets.includes(s.entity_id)) return false;
                        return true;
                      });
                      
                      const allChildren = new Set();
                      Object.values(groups).forEach(children => children.forEach(c => allChildren.add(c)));
                      
                      let topLevelDevices = allDevices.filter(d => !allChildren.has(d.entity_id));
                      topLevelDevices.sort((a, b) => {
                        const aUnavail = a.state === 'unavailable';
                        const bUnavail = b.state === 'unavailable';
                        if (aUnavail && !bUnavail) return 1;
                        if (!aUnavail && bUnavail) return -1;
                        
                        const activeCasts = state?.attributes?.active_casts || [];
                        const aActive = activeCasts.includes(a.entity_id);
                        const bActive = activeCasts.includes(b.entity_id);
                        if (aActive && !bActive) return -1;
                        if (!aActive && bActive) return 1;
                        
                        return (a.attributes.friendly_name || a.entity_id).localeCompare(b.attributes.friendly_name || b.entity_id);
                      });
                      
                      const searchText = (this._castSearchText || '').toLowerCase();
                      if (searchText) {
                        topLevelDevices = topLevelDevices.filter(s => (s.attributes.friendly_name || s.entity_id).toLowerCase().includes(searchText));
                      }
                      
                      return topLevelDevices.map(s => {
                        const isExpanded = this._castExpandedGroups.includes(s.entity_id);
                        const myChildrenIds = groups[s.entity_id] || [];
                        const hasChildren = myChildrenIds.length > 0;
                        
                        let icon = 'mdi:cast-connected';
                        const dclass = s.attributes.device_class;
                        if (dclass === 'tv') icon = 'mdi:television';
                        else if (dclass === 'speaker') icon = 'mdi:speaker';
                        else if (dclass === 'receiver') icon = 'mdi:audio-video';
                        
                        const isActive = state?.attributes?.active_casts && state.attributes.active_casts.includes(s.entity_id);
                        const isUnavailable = s.state === 'unavailable';
                        
                        return html`
                          <div style="display:flex; flex-direction:column; background:${isExpanded ? 'rgba(255,255,255,0.03)' : 'transparent'}; border-radius:8px; transition:all 0.2s;">
                            <div style="display:flex; justify-content:space-between; align-items:center; padding:10px 12px; border-radius:8px; cursor:${isUnavailable ? 'not-allowed' : 'pointer'}; opacity:${isUnavailable ? '0.4' : '1'};" onmouseover="if(!${isUnavailable}) this.style.background='rgba(255,255,255,0.1)'" onmouseout="this.style.background='transparent'">
                              <div style="display:flex; align-items:center; gap:12px; overflow:hidden; flex-grow:1;" @click="${() => { if (!isUnavailable) { isActive ? this._stopCast(currentRoom.entity, s.entity_id) : this._castStream(currentRoom.entity, s.entity_id) } }}">
                                <ha-icon icon="${icon}" style="color:${isActive ? '#2ecc71' : '#aaa'}; --mdc-icon-size:20px; flex-shrink:0;"></ha-icon>
                                <div style="display:flex; flex-direction:column; gap:2px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
                                  <div style="display:flex; align-items:center; gap:6px;">
                                    <span style="color:${isActive ? '#2ecc71' : '#fff'}; font-weight:500; font-size:0.9rem; text-overflow:ellipsis; overflow:hidden;">${s.attributes.friendly_name || s.entity_id}</span>
                                    ${hasChildren ? html`<span style="background:rgba(255,255,255,0.1); color:#ccc; font-size:0.65rem; padding:2px 6px; border-radius:10px; font-weight:bold;">+${myChildrenIds.length}</span>` : ''}
                                  </div>
                                  <span style="color:rgba(255,255,255,0.5); font-size:0.75rem; display:flex; align-items:center; gap:4px; text-overflow:ellipsis; overflow:hidden;">
                                    ${!isUnavailable && s.state !== 'off' ? html`<span style="width:6px; height:6px; border-radius:50%; background:#2ecc71; flex-shrink:0;"></span>` : html`<span style="width:6px; height:6px; border-radius:50%; background:rgba(255,255,255,0.2); flex-shrink:0;"></span>`}
                                    ${s.state.charAt(0).toUpperCase() + s.state.slice(1)} 
                                    ${s.attributes.app_name && !isUnavailable ? html`<span style="text-overflow:ellipsis; overflow:hidden;">• ${s.attributes.app_name}</span>` : ''}
                                  </span>
                                </div>
                              </div>
                              <div style="display:flex; align-items:center; gap:8px; flex-shrink:0;">
                                <div style="width:24px; height:24px; display:flex; justify-content:center; align-items:center; border-radius:50%; background:rgba(255,255,255,0.05); cursor:pointer;" @click="${(e) => { e.stopPropagation(); this._toggleExpandedGroup(s.entity_id); }}">
                                  <ha-icon icon="${isExpanded ? 'mdi:chevron-up' : 'mdi:chevron-down'}" style="color:#fff; --mdc-icon-size:18px;"></ha-icon>
                                </div>
                                <div style="width:20px; height:20px; border-radius:4px; border:2px solid ${isActive ? '#2ecc71' : 'rgba(255,255,255,0.3)'}; background:${isActive ? '#2ecc71' : 'transparent'}; display:flex; justify-content:center; align-items:center; transition:all 0.2s;" @click="${() => { if (!isUnavailable) { isActive ? this._stopCast(currentRoom.entity, s.entity_id) : this._castStream(currentRoom.entity, s.entity_id) } }}">
                                  ${isActive ? html`<ha-icon icon="mdi:check" style="color:#111; --mdc-icon-size:16px;"></ha-icon>` : html``}
                                </div>
                              </div>
                            </div>
                            
                            ${isExpanded ? html`
                              <div style="padding:4px 8px 12px 32px; display:flex; flex-direction:column; gap:4px; border-top:1px solid rgba(255,255,255,0.05); margin-top:4px;">
                                <div style="font-size:0.75rem; color:#aaa; margin-bottom:4px; padding-left:4px;">Group with:</div>
                                ${allDevices.filter(d => d.entity_id !== s.entity_id && (!allChildren.has(d.entity_id) || myChildrenIds.includes(d.entity_id))).sort((a, b) => {
                                  const aUnavail = a.state === 'unavailable';
                                  const bUnavail = b.state === 'unavailable';
                                  if (aUnavail && !bUnavail) return 1;
                                  if (!aUnavail && bUnavail) return -1;
                                  
                                  const activeCasts = state?.attributes?.active_casts || [];
                                  const aActive = activeCasts.includes(a.entity_id);
                                  const bActive = activeCasts.includes(b.entity_id);
                                  if (aActive && !bActive) return -1;
                                  if (!aActive && bActive) return 1;
                                  
                                  return (a.attributes.friendly_name || a.entity_id).localeCompare(b.attributes.friendly_name || b.entity_id);
                                }).map(childCandidate => {
                                  const isChecked = myChildrenIds.includes(childCandidate.entity_id);
                                  return html`
                                    <div style="display:flex; justify-content:space-between; align-items:center; padding:6px 8px; border-radius:6px; cursor:pointer;" onmouseover="this.style.background='rgba(255,255,255,0.05)'" onmouseout="this.style.background='transparent'" @click="${() => this._toggleGroupTarget(s.entity_id, childCandidate.entity_id, isChecked)}">
                                      <span style="color:${isChecked ? '#fff' : '#aaa'}; font-size:0.85rem; text-overflow:ellipsis; overflow:hidden; white-space:nowrap;">${childCandidate.attributes.friendly_name || childCandidate.entity_id}</span>
                                      <div style="width:16px; height:16px; border-radius:3px; border:2px solid ${isChecked ? '#3498db' : 'rgba(255,255,255,0.3)'}; background:${isChecked ? '#3498db' : 'transparent'}; display:flex; justify-content:center; align-items:center; flex-shrink:0;">
                                        ${isChecked ? html`<ha-icon icon="mdi:check" style="color:#111; --mdc-icon-size:14px;"></ha-icon>` : html``}
                                      </div>
                                    </div>
                                  `;
                                })}
                              </div>
                            ` : ''}
                          </div>
                        `;
                      });
                    })()}
                  </div>
                </div>
              ` : html``}
            </div>

            <button class="refresh-btn ${this._refreshInfo ? 'animating-out' : ''}" @click="${() => this._refreshAnalysis(currentRoom.entity)}">
              <ha-icon icon="mdi:refresh" class="refresh-icon"></ha-icon>
            </button>
          </div>
        </div>
        
        <!-- CAMERA POPUP OVERLAY -->
        <div class="live-camera-popup ${this._showCameraOverlay ? '' : 'hidden'}" style="pointer-events:auto;">
          <ha-icon icon="mdi:close" class="live-camera-close" @click="${() => this._showCameraOverlay = false}"></ha-icon>
          ${this._showCameraOverlay && cameraEntityId ? html`
            <ha-camera-stream 
              .hass=${this.hass} 
              .stateObj=${this.hass.states[cameraEntityId]} 
              ?muted=${this._cameraMuted}
              style="width:100%; height:100%; display:block; object-fit:cover; pointer-events:none;">
            </ha-camera-stream>
            
            <div style="position:absolute; bottom:20px; left:50%; transform:translateX(-50%); display:flex; align-items:center; gap:12px; background:rgba(30,30,30,0.85); padding:8px 20px; border-radius:30px; backdrop-filter:blur(10px); z-index:10001; transition:opacity 0.3s; opacity:0; width:60%; border:1px solid rgba(255,255,255,0.1); box-shadow:0 10px 30px rgba(0,0,0,0.5);" 
                 class="camera-controls-overlay">
              
              <ha-icon icon="${this._cameraMuted ? 'mdi:volume-off' : (this._cameraVolume > 0.5 ? 'mdi:volume-high' : 'mdi:volume-medium')}" 
                       style="color:#fff; cursor:pointer; --mdc-icon-size:24px;" 
                       @click="${() => { this._cameraMuted = !this._cameraMuted; this._updateCameraVolume(); }}"></ha-icon>
              
              <input type="range" min="0" max="1" step="0.05" .value="${this._cameraVolume}" 
                     @input="${(e) => { this._cameraVolume = parseFloat(e.target.value); this._cameraMuted = false; this._updateCameraVolume(); }}"
                     style="flex-grow:1; cursor:pointer; accent-color:#1e5c8a;">
            </div>
          ` : html``}
        </div>



        ${isPlaying ? html`
          <div class="ui-layer bottom-display-zone">
            <!-- Left Side: Title and Active Streaming Source -->
            <div class="media-meta-stack">
              <div class="meta-header-row">
                ${seriesTitle == "Browsing..." || displayTitle == "Browsing..." ? html`` : html`
                ${logoUrl ? html`<img class="channel-logo" src="${logoUrl}" />` : html`
                <div style="display:flex; flex-direction:column; gap:4px;">
                  <div class="app-badge">${appName || 'Live Feed'}</div>
                  ${vlmModel ? html`<div style="font-size:0.65rem; color:rgba(255,255,255,0.4); font-weight:700; text-transform:uppercase; letter-spacing:1px;">AI: ${vlmModel} [${confidence}%]</div>` : html``}
                </div>`}
                `}
                <div style="display:flex; flex-direction:column; gap:4px;">
                  ${seriesTitle == "Browsing..." || displayTitle == "Browsing..." ? html`<div style="display:flex;align-items:center;gap:12px;"><h1 class="main-title">Browsing...</h1></div>` : html`
                  ${seriesTitle && displayTitle ? html`<div style="display:flex;align-items:center;gap:12px;"><h1 class="main-title">${seriesTitle}</h1>${contentRating ? html`<div class="content-rating">${contentRating}</div>`:html``}</div><h2 class="sub-title">${displayTitle}</h2>` : html``}
                  ${seriesTitle && !displayTitle ? html`<div style="display:flex;align-items:center;gap:12px;"><h1 class="main-title">${seriesTitle}</h1>${contentRating ? html`<div class="content-rating">${contentRating}</div>`:html``}</div>` : html``}
                  ${displayTitle && !seriesTitle ? html`<div style="display:flex;align-items:center;gap:12px;"><h1 class="main-title">${displayTitle}</h1>${contentRating ? html`<div class="content-rating">${contentRating}</div>`:html``}</div>` : html``}
                  `}
                </div>
              </div>
              
              <!--
              ${sceneAnalysis.visual_description ? html`
                <div class="telemetry-placard">
                  <div class="telemetry-header">AI Scene Telemetry</div>
                  <div class="telemetry-body">
                    ${sceneAnalysis.mood_tags ? html`
                      <div style="margin-bottom:8px;">
                        ${sceneAnalysis.mood_tags.map(tag => html`<span class="dak-badge">${tag}</span>`)}
                      </div>
                    ` : html``}
                    ${sceneAnalysis.visual_description}
                  </div>
                </div>
              ` : html``}-->
            </div>

            <!-- Right Side: Smart Context Card -->
            <div class="context-container">
              ${seriesTitle !== "Browsing..." && displayTitle !== "Browsing..." ? html`
                ${sportsData && Object.keys(sportsData).length > 0 ? this._renderSportsCard(sportsData, state.attributes) : html``}
                ${showInfo && Object.keys(showInfo).length > 0 && (!sportsData || Object.keys(sportsData).length === 0) ? this._renderShowInfoCard(showInfo, sceneAnalysis) : html``}
              ` : html``}
            </div>
          </div>
        ` : html`
          <div class="ui-layer" style="margin-top:auto; color:rgba(255,255,255,0.4); font-size:1.2rem; font-weight:500;">
            Standby • Ready to cast
          </div>
        `}
      </div>
    `;
  }

  _renderBaseballScorebug(sports, attrs) {
    const isLive = sports.is_active;
    const topInning = sports.inning_half === 'top';
    const botInning = sports.inning_half === 'bottom';
    
    // Fallback if not populated
    const b1 = sports.on_first;
    const b2 = sports.on_second;
    const b3 = sports.on_third;
    
    const countStr = (sports.balls !== undefined && sports.strikes !== undefined) ? `${sports.balls}-${sports.strikes}` : "";
    const outs = sports.outs || 0;
    
    return html`
      <div class="baseball-scorebug" style="font-family:'Roboto', sans-serif; display:inline-block; border-radius:4px; overflow:hidden; background:#000; color:#fff; box-shadow:0 10px 30px rgba(0,0,0,0.8); width:100%; max-width:800px; border:2px solid rgba(255,255,255,0.2);">
        
        <!-- TOP BAR: Batter Info -->
        <div style="display:flex; justify-content:space-between; align-items:center; background:#111; padding:6px 16px; border-bottom:2px solid rgba(255,255,255,0.2); font-weight:bold; letter-spacing:1px; text-transform:uppercase;">
          <div style="font-size:1.1rem; color:#fff;">${sports.batter_name || 'BATTER'}</div>
          <div style="font-size:0.95rem; color:#ccc;">${sports.batter_summary || ''}</div>
        </div>
        
        <div style="display:flex; height:120px;">
          
          <!-- LEFT COLUMN: Teams -->
          <div style="flex:1; display:flex; flex-direction:column; border-right:2px solid rgba(255,255,255,0.2);">
            <div style="flex:1; background:${sports.away_color || '#005A9C'}; display:flex; align-items:center; justify-content:space-between; padding:0 16px; border-bottom:2px solid rgba(255,255,255,0.2);">
              <div style="display:flex; align-items:center; gap:12px;">
                ${sports.away_logo ? html`<img src="${sports.away_logo}" style="width:40px; height:40px; object-fit:contain; filter: drop-shadow(0 -1px 0 rgba(255,255,255,0.75)) drop-shadow(-1px 0 0 rgba(255,255,255,0.75)) drop-shadow(1px 0 0 rgba(255,255,255,0.75)) drop-shadow(0 1px 0 rgba(255,255,255,0.75)) drop-shadow(0 2px 4px rgba(0,0,0,0.5));">` : html``}
                <span style="font-size:1.8rem; font-weight:800; letter-spacing:2px; text-shadow:0 2px 4px rgba(0,0,0,0.5);">${sports.away_team}</span>
              </div>
              <span style="font-size:2.2rem; font-weight:800; text-shadow:0 2px 4px rgba(0,0,0,0.5);">${sports.away_score}</span>
            </div>
            <div style="flex:1; background:${sports.home_color || '#EB6E1F'}; display:flex; align-items:center; justify-content:space-between; padding:0 16px;">
              <div style="display:flex; align-items:center; gap:12px;">
                ${sports.home_logo ? html`<img src="${sports.home_logo}" style="width:40px; height:40px; object-fit:contain; filter: drop-shadow(0 -1px 0 rgba(255,255,255,0.75)) drop-shadow(-1px 0 0 rgba(255,255,255,0.75)) drop-shadow(1px 0 0 rgba(255,255,255,0.75)) drop-shadow(0 1px 0 rgba(255,255,255,0.75)) drop-shadow(0 2px 4px rgba(0,0,0,0.5));">` : html``}
                <span style="font-size:1.8rem; font-weight:800; letter-spacing:2px; text-shadow:0 2px 4px rgba(0,0,0,0.5);">${sports.home_team}</span>
              </div>
              <span style="font-size:2.2rem; font-weight:800; text-shadow:0 2px 4px rgba(0,0,0,0.5);">${sports.home_score}</span>
            </div>
          </div>
          
          <!-- MIDDLE COLUMN: Bases & Inning -->
          <div style="width:160px; display:flex; background:#111; border-right:2px solid rgba(255,255,255,0.2);">
            
            <!-- Inning Arrows -->
            <div style="width:40px; display:flex; flex-direction:column; justify-content:center; align-items:center; border-right:1px solid rgba(255,255,255,0.1);">
              <div style="width:0; height:0; border-left:8px solid transparent; border-right:8px solid transparent; border-bottom:10px solid ${topInning ? '#f1c40f' : '#555'}; margin-bottom:4px;"></div>
              <div style="font-size:1.8rem; font-weight:800; line-height:1;">${sports.period}</div>
              <div style="width:0; height:0; border-left:8px solid transparent; border-right:8px solid transparent; border-top:10px solid ${botInning ? '#f1c40f' : '#555'}; margin-top:4px;"></div>
            </div>
            
            <!-- Bases & Outs -->
            <div style="flex:1; display:flex; flex-direction:column; justify-content:center; align-items:center; padding-top:10px;">
              <div style="position:relative; width:60px; height:60px; transform:rotate(45deg); margin-bottom:10px;">
                <!-- 2nd Base -->
                <div style="position:absolute; top:0; left:0; width:22px; height:22px; background:${b2 ? '#f1c40f' : '#555'}; border:1px solid #222;"></div>
                <!-- 3rd Base -->
                <div style="position:absolute; bottom:0; left:0; width:22px; height:22px; background:${b3 ? '#f1c40f' : '#555'}; border:1px solid #222;"></div>
                <!-- 1st Base -->
                <div style="position:absolute; top:0; right:0; width:22px; height:22px; background:${b1 ? '#f1c40f' : '#555'}; border:1px solid #222;"></div>
              </div>
              <div style="display:flex; gap:6px;">
                <div style="width:10px; height:10px; border-radius:50%; background:${outs >= 1 ? '#f1c40f' : '#555'};"></div>
                <div style="width:10px; height:10px; border-radius:50%; background:${outs >= 2 ? '#f1c40f' : '#555'};"></div>
                <div style="width:10px; height:10px; border-radius:50%; background:${outs >= 3 ? '#f1c40f' : '#555'};"></div>
              </div>
            </div>
          </div>
          
          <!-- RIGHT COLUMN: Count & Pitcher -->
          <div style="width:200px; display:flex; flex-direction:column; background:#111;">
            <div style="flex:1; display:flex; justify-content:center; align-items:center; font-size:2.4rem; font-weight:800; border-bottom:1px solid rgba(255,255,255,0.2);">
              ${countStr}
            </div>
            <div style="height:45px; display:flex; flex-direction:column; justify-content:center; align-items:center; padding:4px;">
              <span style="font-size:0.8rem; color:#aaa; font-weight:bold; letter-spacing:1px; text-transform:uppercase;">${sports.pitcher_name || 'PITCHER'}</span>
              <span style="font-size:0.9rem; font-weight:bold; color:#fff;">${sports.pitcher_summary || ''}</span>
            </div>
          </div>
          
        </div>
      </div>
    `;
  }

  _renderSportsCard(sports, attrs) {
    if (sports.sport === 'baseball') {
      return this._renderBaseballScorebug(sports, attrs);
    }
    const isLive = sports.is_active;
    const vlmGameState = sports.vlm_game_state || attrs?.extra_attributes?.sports_info?.game_state || {};

    // Only golf uses the leaderboard view
    if (sports.sport === 'golf' && sports.is_individual && sports.leaderboard && sports.leaderboard.length > 0) {
      let gameTimeHtml = html`<div class="game-time">${sports.status_detail || 'Round ' + (sports.period || 1)}</div>`;
      
      return html`
        <div class="sports-details-card">
          <div class="sports-header">
            <div class="game-status ${isLive ? 'live' : ''}">
              ${isLive ? 'Live Tournament' : sports.status_detail || 'Upcoming'}
            </div>
            ${gameTimeHtml}
          </div>
          
          <div class="teams-container" style="display:flex; flex-direction:column; gap:10px;">
            ${sports.leaderboard.map((player, index) => html`
              <div style="display:flex; align-items:center; justify-content:space-between; background:rgba(0,0,0,0.3); padding:12px 20px; border-radius:12px; border:1px solid rgba(255,255,255,0.05);">
                <div style="display:flex; align-items:center; gap:20px;">
                  ${player.flag ? html`<img src="${player.flag}" style="width:28px; border-radius:4px; filter:drop-shadow(0 2px 4px rgba(0,0,0,0.5));">` : html`<span style="font-size:1.4rem; font-weight:800; color:${index === 0 ? '#f5c518' : 'rgba(255,255,255,0.4)'}; width:28px; text-align:center;">${player.rank}</span>`}
                  <span style="font-size:1.3rem; font-weight:700; color:#fff; letter-spacing:0.5px;">${player.name}</span>
                </div>
                <div style="display:flex; align-items:center; gap:30px;">
                  <span style="font-size:1.6rem; font-weight:800; color:${index === 0 ? '#f5c518' : '#fff'}; width:40px; text-align:right;">${player.score}</span>
                  <div style="display:flex; flex-direction:column; align-items:center; width:40px;">
                    <span style="font-size:0.6rem; color:rgba(255,255,255,0.5); font-weight:800; text-transform:uppercase; letter-spacing:1px; margin-bottom:2px;">Thru</span>
                    <span style="font-size:1.2rem; font-weight:800; color:#fff;">${player.thru}</span>
                  </div>
                </div>
              </div>
            `)}
          </div>
          ${attrs.venue || attrs.league || attrs.tournament_name ? html`<div style="margin-top:20px; font-size:0.75rem; color:rgba(255,255,255,0.3); text-transform:uppercase; font-weight:800; letter-spacing:1.5px; text-align:center;">${attrs.tournament_name || attrs.league} ${attrs.venue ? html`• ${attrs.venue}` : html``}</div>` : html``}
        </div>
      `;
    }

    const renderScoresDisplay = (score, linescores, hits, errors, opponentLinescores, isAway) => {
      if (sports.sport === 'baseball' && hits !== undefined && hits !== "") {
        return html`
          <div style="display:flex; align-items:center; gap:20px;">
            <div style="display:flex; gap:16px; font-size:1.1rem; color:rgba(255,255,255,0.7); padding-right:0px; border-right:0px solid rgba(255,255,255,0.2);">
              <div class="score-column">
                <span style="font-size:1.0rem; text-transform:uppercase; font-weight:800; color:rgba(255,255,255,0.4);">R</span>
                <span style="color:#fff; font-weight:bold; font-size:1.5rem;">${score}</span>
              </div>
              <div class="score-column">
                <span style="font-size:1.0rem; text-transform:uppercase; font-weight:800; color:rgba(255,255,255,0.4);">H</span>
                <span style="font-size:1.5rem;">${hits || '0'}</span>
              </div>
              <div class="score-column">
                <span style="font-size:1.0rem; text-transform:uppercase; font-weight:800; color:rgba(255,255,255,0.4);">E</span>
                <span style="font-size:1.5rem;">${errors || '0'}</span>
              </div>
            </div>
          </div>
        `;
      } else if (sports.sport === 'tennis') {
        const hasNativeLinescores = linescores && linescores.length > 0;
        
        let displayGameScore = "-";
        if (vlmGameState.tennis_game && vlmGameState.tennis_game !== "null") {
            const parts = vlmGameState.tennis_game.split('-');
            if (parts.length === 2) {
                displayGameScore = isAway ? parts[0].trim() : parts[1].trim();
            } else {
                displayGameScore = vlmGameState.tennis_game;
            }
        }
        if (displayGameScore === "null" || displayGameScore === "") displayGameScore = "-";

        return html`
          <div style="display:flex; align-items:center; gap:12px;">
            <div style="display:flex; gap:12px; padding-right:16px; border-right:0px solid rgba(255,255,255,0.2);">
              ${hasNativeLinescores ? linescores.map((ls, idx) => {
                  const opp_ls = opponentLinescores && opponentLinescores.length > idx ? opponentLinescores[idx] : null;
                  const myScore = parseInt(ls) || 0;
                  const oppScore = parseInt(opp_ls) || 0;
                  const isWinner = (myScore >= 6 && myScore - oppScore >= 2) || (myScore === 7 && oppScore === 6);
                  return html`<span class="primary-score" style="color:${isWinner ? '#f5c518' : 'rgba(255,255,255,0.6)'}; font-weight:${isWinner ? 'bold' : 'normal'}; width:20px; text-align:center;">${ls}</span>`;
              }) : html``}
            </div>
            <!--<div class="primary-score" style="color:#f5c518; margin-right:8px; width:50px; text-align:right;">${displayGameScore}</div>-->
          </div>
        `;
      }
      return html`<div class="primary-score">${score}</div>`;
    };

    let gameTimeHtml = html`<div class="game-time">${sports.period_name || 'Period ' + sports.period}</div>`;
    if (sports.sport === 'golf') {
      gameTimeHtml = html`<div class="game-time">${sports.status_detail || 'Round ' + sports.period}</div>`;
    }
    else if (sports.sport === 'tennis') {
      gameTimeHtml = html``;
    } else if (sports.clock) {
      gameTimeHtml = html`<div class="game-time">${sports.clock} • ${sports.period_name || 'Period ' + sports.period}</div>`;
    }

    return html`
      <div class="sports-details-card">
        <div class="sports-header">
          <div class="game-status ${isLive ? 'live' : ''}">
            ${isLive ? (sports.sport === 'golf' ? 'Live Tournament' : 'Live Match') : sports.status_detail || 'Upcoming'}
          </div>
          ${gameTimeHtml}
        </div>

        <div class="teams-container">
          <div class="team-row">
            <div class="team-identity">
              ${sports.away_flag ? html`<img class="flag-icon" src="${sports.away_flag}">` : html``}
              ${!sports.is_individual && sports.away_logo ? html`<img class="team-logo" src="${sports.away_logo}">` : html``}
              <span class="team-name">
                ${sports.away_team}
                ${sports.away_seed ? html`<span class="team-seed">#${sports.away_seed}</span>` : html``}
              </span>
              ${sports.sport === 'tennis' && vlmGameState.server === sports.away_team ? html`<div style="width:8px; height:8px; background:#f5c518; border-radius:50%; margin-left:8px; box-shadow: 0 0 8px #f5c518;"></div>` : html``}
            </div>
            <div class="scores-display">
              ${renderScoresDisplay(sports.away_score, sports.away_linescores, sports.away_hits, sports.away_errors, sports.home_linescores, true)}
            </div>
          </div>

          <div class="team-row ${sports.possession === sports.home_team ? 'has-possession' : ''}">
            <div class="team-identity">
              ${sports.home_flag ? html`<img class="flag-icon" src="${sports.home_flag}">` : html``}
              ${!sports.is_individual && sports.home_logo ? html`<img class="team-logo" src="${sports.home_logo}">` : html``}
              <span class="team-name">
                ${sports.home_team}
                ${sports.home_seed ? html`<span class="team-seed">#${sports.home_seed}</span>` : html``}
              </span>
              ${sports.sport === 'tennis' && vlmGameState.server === sports.home_team ? html`<div style="width:8px; height:8px; background:#f5c518; border-radius:50%; margin-left:8px; box-shadow: 0 0 8px #f5c518;"></div>` : html``}
            </div>
            <div class="scores-display">
              ${renderScoresDisplay(sports.home_score, sports.home_linescores, sports.home_hits, sports.home_errors, sports.away_linescores, false)}
            </div>
          </div>
        </div>
        ${attrs.venue || attrs.league || attrs.tournament_name ? html`<div style="margin-top:16px; font-size:0.75rem; color:rgba(255,255,255,0.4); text-transform:uppercase; font-weight:700; letter-spacing:1px; text-align:center;">${attrs.tournament_name || attrs.league} ${attrs.venue ? html`• ${attrs.venue}` : html``}</div>` : html``}
      </div>
    `;
  }

  _renderShowInfoCard(info, scene) {
    const cleanRating = info.rating ? parseFloat(info.rating).toFixed(1) : null;
    const minutes = info.runtime ? parseInt(info.runtime) : null;
    const actors = info.actors || [];
    const episode = (info.episode && info.episode.length > 0) ? info.episode[0] : null;

    return html`
      <div class="show-details-card">
        <div class="show-meta-badges">
          ${cleanRating ? html`<div class="rating-badge">★ ${cleanRating}/10</div>` : html``}
          ${(info.genres || []).slice(0, 3).map(g => html`<div class="genre-badge">${g}</div>`)}
          ${minutes ? html`<div class="duration-badge">⏱ ${minutes} min</div>` : html``}
        </div>
        ${episode ? html`<div style="font-size:1.1rem; color:#fff; font-weight:800; margin-bottom:8px;">${episode}</div>` : html``}
        ${info.overview ? html`<p class="show-overview">${info.overview}</p>` : html``}
        ${actors.length > 0 ? html`
          <div style="margin-top:8px; border-top:1px solid rgba(255,255,255,0.1); padding-top:12px;">
            <div style="font-size:0.65rem; color:rgba(255,255,255,0.4); font-weight:800; text-transform:uppercase; letter-spacing:1px; margin-bottom:8px;">Starring</div>
            <div style="font-size:0.85rem; color:#fff; display:flex; flex-wrap:wrap; gap:10px;">
              ${actors.map(actor => html`<span>${actor}</span>`)}
            </div>
          </div>
        ` : html``}
      </div>
    `;
  }
}

// ============================================================================
// 2. STABLE NATIVE EDITOR (iOS COMPATIBLE)
// ============================================================================
class AiMediaMatrixCardEditor extends LitElement {
  static get properties() { return { hass: {}, _config: {}, _discovered: { type: Array } }; }
  setConfig(config) { this._config = config || { rooms: [] }; }
  
  updated(changed) {
    if (changed.has('hass') && this.hass) {
      this._discovered = Object.keys(this.hass.states)
        .filter(id => id.startsWith('media_player.ai_media_') || id.startsWith('media_player.ai_'))
        .map(id => ({ entity: id, name: this.hass.states[id].attributes.friendly_name || id }));
    }
  }

  render() {
    if (!this.hass) return html``;
    return html`
      <div style="padding: 10px;">
        <h3>Select AI Media Proxies</h3>
        ${(this._discovered || []).map(dev => {
            const isEnabled = (this._config.rooms || []).some(r => r.entity === dev.entity);
            return html`
                <div style="display:flex; justify-content:space-between; padding:10px 0; align-items:center;">
                    <span>${dev.name}</span>
                    <input type="checkbox" ?checked="${isEnabled}" @change="${(e) => this._toggle(dev, e.target.checked)}">
                </div>
            `;
        })}
      </div>
    `;
  }

  _toggle(dev, active) {
    let rooms = [...(this._config.rooms || [])];
    rooms = active ? [...rooms, dev] : rooms.filter(r => r.entity !== dev.entity);
    this.dispatchEvent(new CustomEvent('config-changed', { detail: { config: { ...this._config, rooms } }, bubbles: true, composed: true }));
  }
}

customElements.define('ai-media-matrix-card', AiMediaMatrixCard);
customElements.define('ai-media-matrix-card-editor', AiMediaMatrixCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'ai-media-matrix-card',
  name: 'AI Media Matrix Card',
  description: 'DAKboard style AI media display with Live Scores & Cinematic Details',
  preview: true
});

