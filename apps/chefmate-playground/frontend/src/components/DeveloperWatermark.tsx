import { useEffect, useState } from 'react';
import { api } from '../runtime/http';
import { Icon } from './Icon';

const clock = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
export function DeveloperWatermark() {
  const [version, setVersion] = useState(''), [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const abort = new AbortController();
    void api('/api/about', { signal: abort.signal }).then(data => { if (!abort.signal.aborted) setVersion(typeof data.version === 'string' ? data.version : ''); }).catch(() => {});
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => { abort.abort(); clearInterval(timer); };
  }, []);
  return <div className="developer-watermark" aria-label="开发者模式标识"><Icon name="code-xml" /><span>开发模式{version ? ` · v${version}` : ''}</span><time dateTime={now.toISOString()}>{clock.format(now)}</time></div>;
}
