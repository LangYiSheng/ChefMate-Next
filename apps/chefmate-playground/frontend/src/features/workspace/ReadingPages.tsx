/** Long directions stay in one scrollable reading area, without another page control. */
export function ReadingPages({ text, label = '阅读内容' }: { text: string; label?: string }) {
  return <div className="workspace-reading-pages"><div className="workspace-reading-viewport" role="region" aria-label={label} tabIndex={0}><p className="ui-step-instruction">{text}</p></div></div>;
}
