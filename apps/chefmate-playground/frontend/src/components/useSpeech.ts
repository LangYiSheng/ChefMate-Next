import { useEffect, useRef, useState } from 'react';

type Phase = 'idle' | 'connecting' | 'recording' | 'finishing';
type Session = { ws?: WebSocket; stream?: MediaStream; context?: AudioContext; node?: AudioWorkletNode;
  timer?: ReturnType<typeof setTimeout>; base: string; text: string; stopped: boolean;
  resolve?: (text: string | null) => void; finish?: Promise<string | null> };

export function useSpeech(key: string, onText: (text: string) => void, notify: (text: string) => void) {
  const [phase, setPhase] = useState<Phase>('idle');
  const [level, setLevel] = useState(0);
  const current = useRef<Session | null>(null);
  const callbacks = useRef({ onText, notify }); callbacks.current = { onText, notify };
  function release(s: Session) {
    clearTimeout(s.timer); s.stream?.getTracks().forEach(t => t.stop());
    s.node?.disconnect(); if (s.context && s.context.state !== 'closed') void s.context.close();
  }
  function end(s: Session, ok: boolean, message?: string) {
    if (current.current !== s) return;
    current.current = null; release(s); s.ws?.close(); s.resolve?.(ok ? s.text : null);
    setPhase('idle'); setLevel(0); if (message) callbacks.current.notify(message);
  }
  useEffect(() => {
    const cancel = () => { const s = current.current; if (s) end(s, false); };
    const hidden = () => { if (document.hidden) cancel(); };
    window.addEventListener('pagehide', cancel); document.addEventListener('visibilitychange', hidden);
    return () => { cancel(); window.removeEventListener('pagehide', cancel); document.removeEventListener('visibilitychange', hidden); };
  }, [key]);
  async function start(base: string) {
    if (current.current) return;
    if (!navigator.mediaDevices?.getUserMedia) { callbacks.current.notify('此浏览器无法录音，请使用 HTTPS 或本机地址并允许麦克风。'); return; }
    const s: Session = { base, text: base, stopped: false }; current.current = s; setPhase('connecting');
    s.timer = setTimeout(() => end(s, false, '麦克风或语音连接超时，请重试。'), 20000);
    try {
      s.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
      if (current.current !== s) { release(s); return; }
      s.context = new AudioContext();
      await s.context.audioWorklet.addModule('/static/speech-worklet.js');
      await s.context.resume();
      if (current.current !== s) { release(s); return; }
      const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/speech/stream`); s.ws = ws;
      ws.onmessage = event => {
        if (current.current !== s) return;
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'ready') {
            clearTimeout(s.timer); setPhase('recording');
            const node = new AudioWorkletNode(s.context!, 'speech-pcm'); s.node = node;
            node.port.onmessage = ({ data }) => {
              if (current.current !== s) return;
              if (ws.bufferedAmount > 128000) { end(s, false, '网络较慢，录音已停止，文字已保留。'); return; }
              if (ws.readyState === WebSocket.OPEN) {
                if (data.pcm.byteLength) ws.send(data.pcm);
                if (data.final) { ws.send(JSON.stringify({ type: 'stop' })); release(s);
                  s.timer = setTimeout(() => end(s, false, '转写收尾超时，文字已保留，请检查后重新发送。'), 7000); }
              }
              setLevel(Math.min(1, data.level * 8));
            };
            s.context!.createMediaStreamSource(s.stream!).connect(node); node.connect(s.context!.destination);
            s.timer = setTimeout(() => { void stop(); }, 175000);
          } else if (msg.type === 'partial' || msg.type === 'final') {
            s.text = (s.base + (s.base && msg.text ? '\n' : '') + msg.text).slice(0, 8000);
            callbacks.current.onText(s.text);
            if (msg.type === 'final') end(s, true);
          } else if (msg.type === 'error') end(s, false, msg.message);
        } catch { end(s, false, '语音响应异常，已保留文字。'); }
      };
      ws.onerror = () => end(s, false, '无法连接语音服务，请检查登录状态后重试。');
      ws.onclose = () => end(s, false, '语音连接已关闭，已保留文字。');
    } catch { end(s, false, '无法使用麦克风，请检查浏览器权限和音频设备。'); }
  }
  function stop(): Promise<string | null> {
    const s = current.current; if (!s) return Promise.resolve(null);
    if (s.finish) return s.finish;
    if (!s.node || s.ws?.readyState !== WebSocket.OPEN) { end(s, false); return Promise.resolve(null); }
    s.stopped = true; clearTimeout(s.timer); s.stream?.getTracks().forEach(t => t.stop()); setPhase('finishing'); setLevel(0);
    s.finish = new Promise(resolve => { s.resolve = resolve; });
    s.node.port.postMessage('flush');
    s.timer = setTimeout(() => end(s, false, '转写收尾超时，文字已保留，请检查后重新发送。'), 7000);
    return s.finish;
  }
  return { phase, level, start, stop };
}
