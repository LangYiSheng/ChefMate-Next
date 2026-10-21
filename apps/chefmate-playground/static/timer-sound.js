const preferenceKey = 'chefmate:kitchen-preferences';

export function readKitchenPreferences(storage = globalThis.localStorage) {
  try { return { timer_sound: JSON.parse(storage?.getItem(preferenceKey) || '{}').timer_sound !== false }; }
  catch { return { timer_sound: true }; }
}

export function saveKitchenPreferences(values, storage = globalThis.localStorage) {
  storage?.setItem(preferenceKey, JSON.stringify({ timer_sound: values.timer_sound !== false }));
}

// A local, gentle three-part chime: no download or network request at timer expiry.
export class TimerSound {
  constructor(scope = globalThis, enabled = () => readKitchenPreferences().timer_sound) {
    this.scope = scope; this.enabled = enabled; this.context = null; this.nodes = [];
    const unlock = () => { this.unlock().catch(() => {}); };
    scope.document?.addEventListener('pointerdown', unlock, { passive: true });
    scope.document?.addEventListener('keydown', unlock, { passive: true });
  }
  async unlock() {
    const Audio = this.scope.AudioContext || this.scope.webkitAudioContext;
    if (!Audio) return false;
    this.context ||= new Audio();
    if (this.context.state === 'suspended') await this.context.resume();
    return this.context.state === 'running';
  }
  stop() {
    for (const oscillator of this.nodes) { try { oscillator.stop(); } catch {} }
    this.nodes = [];
  }
  async play({ preview = false } = {}) {
    if (!preview && !this.enabled()) return false;
    try {
      if (!await this.unlock()) return false;
      // Preferences may have changed while waiting for browser audio permission.
      if (!preview && !this.enabled()) return false;
      this.stop();
      const audio = this.context, start = audio.currentTime + .025;
      for (let repeat = 0; repeat < 3; repeat++) {
        for (const [offset, frequency] of [[0, 660], [.22, 880]]) {
          const oscillator = audio.createOscillator(), gain = audio.createGain();
          const at = start + repeat * .95 + offset;
          oscillator.type = 'sine'; oscillator.frequency.value = frequency;
          gain.gain.setValueAtTime(0, at);
          gain.gain.linearRampToValueAtTime(.16, at + .025);
          gain.gain.exponentialRampToValueAtTime(.001, at + .38);
          oscillator.connect(gain); gain.connect(audio.destination);
          oscillator.start(at); oscillator.stop(at + .4);
          oscillator.onended = () => { oscillator.disconnect(); gain.disconnect(); this.nodes = this.nodes.filter(node => node !== oscillator); };
          this.nodes.push(oscillator);
        }
      }
      return true;
    } catch { return false; }
  }
}
