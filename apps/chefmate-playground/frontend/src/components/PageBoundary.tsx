import {Component, type ReactNode} from 'react';

export type PageBoundaryProps = {
  children: ReactNode;
  onRetry: () => void;
  onBack: () => void;
  pageName?: string;
};

type PageBoundaryState = {failed: boolean};

/** The parent changes this boundary's key and lazy instance when retrying. */
export class PageBoundary extends Component<PageBoundaryProps, PageBoundaryState> {
  state: PageBoundaryState = {failed: false};

  static getDerivedStateFromError(): PageBoundaryState {
    return {failed: true};
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return <section className="page-boundary" role="alert" aria-label="页面暂时没有打开" style={{
      flex: 1, minWidth: 0, minHeight: 0, overflow: 'auto', display: 'grid', alignContent: 'center',
      justifyItems: 'center', gap: 14, padding: '32px 24px', textAlign: 'center',
    }}>
      <h2 style={{margin: 0, fontFamily: 'var(--font-title)', fontWeight: 'var(--font-title-weight)', fontSize: 24, lineHeight: 1.5}}>
        {this.props.pageName ? `${this.props.pageName}暂时没有打开` : '这个页面暂时没有打开'}
      </h2>
      <p style={{maxWidth: 420, margin: 0, color: 'var(--color-muted)', fontFamily: 'var(--font-ui)', fontSize: 14, lineHeight: 1.85}}>
        加载页面时遇到了一点问题，可以重试，也可以先返回对话。
      </p>
      <div style={{display: 'flex', justifyContent: 'center', flexWrap: 'wrap', gap: 10, marginTop: 8}}>
        <button type="button" className="secondary-button" onClick={this.props.onBack}>返回对话</button>
        <button type="button" className="primary-button" onClick={this.props.onRetry}>重试页面</button>
      </div>
    </section>;
  }
}

export default PageBoundary;
