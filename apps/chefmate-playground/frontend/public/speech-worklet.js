// Resample the actual device rate to 16 kHz and emit 200 ms PCM frames.
class SpeechProcessor extends AudioWorkletProcessor {
  constructor() { super(); this.samples = []; this.position = 0; this.frame = []; this.done = false;
    this.port.onmessage = () => {
      this.done = true;
      const pcm = new Int16Array(this.frame);
      this.port.postMessage({ pcm: pcm.buffer, level: 0, final: true }, [pcm.buffer]);
      this.frame = [];
    };
  }
  process(inputs) {
    if (this.done) return true;
    const input = inputs[0]?.[0];
    if (!input) return true;
    this.samples.push(...input);
    const step = sampleRate / 16000;
    while (this.position + 1 < this.samples.length) {
      const i = Math.floor(this.position), f = this.position - i;
      const value = this.samples[i] * (1 - f) + this.samples[i + 1] * f;
      this.frame.push(Math.round(Math.max(-1, Math.min(1, value)) * 32767));
      this.position += step;
      if (this.frame.length === 3200) {
        const pcm = new Int16Array(this.frame);
        const level = Math.sqrt(this.frame.reduce((sum, n) => sum + (n / 32768) ** 2, 0) / 3200);
        this.port.postMessage({ pcm: pcm.buffer, level }, [pcm.buffer]); this.frame = [];
      }
    }
    const used = Math.floor(this.position); this.samples.splice(0, used); this.position -= used;
    return true;
  }
}
registerProcessor('speech-pcm', SpeechProcessor);
