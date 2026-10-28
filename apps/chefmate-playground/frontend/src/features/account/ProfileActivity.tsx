import {useEffect, useState} from 'react';
import {api} from '../../runtime/http';
import {UsageCalendar} from '../settings/SettingsInsights';

export function ProfileActivity({owner, active}: {owner: string; active: boolean}) {
  const [data, setData] = useState<any>(null), [error, setError] = useState(''), [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController(); setData(null); setError('');
    Promise.resolve(api('/api/usage?days=365', {signal: controller.signal})).then(result => {
      if (!controller.signal.aborted) setData(result);
    }).catch(problem => {if (!controller.signal.aborted) setError(problem.message);});
    return () => controller.abort();
  }, [owner, active, retry]);
  return <section className="account-profile-activity" aria-label="Token 活动">
    <div className="account-profile-section-heading"><h2>Token 活动</h2><span>最近一年</span></div>
    {data ? <UsageCalendar data={data}/> : <p className="account-activity-status" role="status">{error || '正在读取活动…'}{error && <button type="button" className="text-button" onClick={() => setRetry(value => value + 1)}>重试</button>}</p>}
  </section>;
}
