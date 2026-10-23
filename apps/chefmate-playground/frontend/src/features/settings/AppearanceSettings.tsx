import {useEffect, useState, type CSSProperties} from 'react';
import {PALETTES, FONT_PAIRINGS, appearanceTokens, resolveAppearance} from './appearance-presets';
import type {Values} from './settings-state';

export function AppearanceSettings({values, change, disabled = false}: {values: Values; change: (key: string, value: any) => void; disabled?: boolean}) {
  const [systemDark, setSystemDark] = useState(globalThis.matchMedia?.('(prefers-color-scheme: dark)').matches || false);
  const [previewMode, setPreviewMode] = useState<string | null>(null);
  const [previewOpen, setPreviewOpen] = useState(globalThis.matchMedia?.('(min-width:1100px)').matches || false);
  useEffect(() => { const media = window.matchMedia?.('(prefers-color-scheme: dark)'); const listener = () => setSystemDark(media.matches); media?.addEventListener('change', listener); return () => media?.removeEventListener('change', listener); }, []);
  useEffect(() => {setPreviewMode(null);}, [values.theme]);
  const resolved = resolveAppearance(values, systemDark);
  const mode = values.theme === 'system' ? previewMode || resolved.theme : resolved.theme;
  const palette = PALETTES.find(item => item.id === values[`${mode}_palette`]) || resolved.palette;
  const style = {...appearanceTokens(palette, resolved.fonts), colorScheme: mode} as CSSProperties;
  return <div className="appearance-layout">
    <div className="appearance-options">
      {(['light', 'dark'] as const).filter(mode => values.theme === 'system' || mode === resolved.theme).map(mode => <fieldset className="palette-group" key={mode}><legend className="appearance-label">{mode === 'light' ? '浅色配色' : '暗色配色'}</legend><div className="palette-grid">
        {PALETTES.filter(item => item.mode === mode).map(item => <label className={`palette-choice ${values[`${mode}_palette`] === item.id ? 'selected' : ''}`} key={item.id}>
          <input type="radio" name={`${mode}_palette`} value={item.id} disabled={disabled} checked={values[`${mode}_palette`] === item.id} onChange={() => {change(`${mode}_palette`, item.id); setPreviewMode(mode);}}/>
          <span className="palette-swatch" aria-hidden="true" style={appearanceTokens(item, FONT_PAIRINGS[0]) as CSSProperties}><span className="palette-mini-sidebar"/><span className="palette-mini-page"><span className="palette-mini-heading"/><span className="palette-mini-line"/><span className="palette-mini-line short"/><span className="palette-mini-button"/></span></span>
          <span className="palette-caption">{item.name}<span aria-hidden="true">{values[`${mode}_palette`] === item.id ? '✓' : ''}</span></span>
        </label>)}
      </div></fieldset>)}
      <fieldset className="font-group"><legend className="appearance-label">字体搭配</legend><div className="font-pairing-grid">{FONT_PAIRINGS.map(font => <label key={font.id} className={`font-choice ${font.id === values.font_pairing ? 'selected' : ''}`}><input type="radio" name="font_pairing" value={font.id} disabled={disabled} checked={values.font_pairing === font.id} onChange={() => change('font_pairing', font.id)}/><span className="font-choice-specimen" style={{fontFamily: font.title, fontWeight: font.titleWeight}}>一餐一味</span><span className="font-choice-copy"><span className="font-choice-name">{font.name}</span><small>{font.note}</small></span></label>)}</div></fieldset>
    </div>
    <details className="appearance-preview-area" open={previewOpen} onToggle={event => setPreviewOpen(event.currentTarget.open)}><summary className="appearance-preview-heading"><span>效果预览</span><span>{palette.name} · {resolved.fonts.name}</span></summary><div className="appearance-preview" style={style} data-palette={palette.id} data-font-pairing={resolved.fonts.id}><div className="appearance-preview-bar"><span className="settings-brand-mark appearance-brand-icon" aria-hidden="true"/>ChefMate</div><div className="appearance-preview-body"><h3 className="appearance-preview-title">把每一餐，照顾好。</h3><span className="appearance-preview-caption">今日饮食 · 示例数据</span><dl className="appearance-preview-metrics"><div><dt>热量</dt><dd>385.2 <small>kcal</small></dd></div><div><dt>蛋白质</dt><dd>26.2 <small>g</small></dd></div></dl><div className="appearance-preview-chat"><strong>小锅搭子</strong><p>辛苦啦，炒滑蛋已经做好，也帮你记下营养了。口感还合胃口吗？</p></div><span className="appearance-preview-action">看看最近的营养</span></div></div><p className="appearance-preview-note">选择后预览，保存后应用到整个工作台。</p></details>
  </div>;
}
