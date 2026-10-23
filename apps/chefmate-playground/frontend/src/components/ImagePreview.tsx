import { useEffect } from 'react';
import { Dialog } from './Dialog';
import { Icon } from './Icon';

export function imageURL(sessionId: string | null, image: Record<string, any>): string {
  return image.previewUrl || image.url || `/api/sessions/${encodeURIComponent(sessionId || '')}/images/${encodeURIComponent(image.id)}`;
}

export function ImagePreview({ images, index, sessionId, onIndex, onClose }: {
  images: Record<string, any>[]; index: number; sessionId: string | null; onIndex: (index: number) => void; onClose: () => void;
}) {
  const image = images[index];
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.key === 'ArrowLeft' && index > 0) { event.preventDefault(); onIndex(index - 1); }
      if (event.key === 'ArrowRight' && index < images.length - 1) { event.preventDefault(); onIndex(index + 1); }
    };
    document.addEventListener('keydown', key); return () => document.removeEventListener('keydown', key);
  }, [images.length, index, onIndex]);
  if (!image) return null;
  return <Dialog title="图片预览" className="image-preview" onClose={onClose}>
    <header className="image-preview-head"><h2>{image.name || '图片预览'}</h2><button type="button" className="icon-button" aria-label="关闭图片预览" onClick={onClose}><Icon name="x" /></button></header>
    <img src={imageURL(sessionId, image)} alt={image.name || '图片'} />
    <div className="image-preview-actions"><button type="button" className="secondary-button" disabled={index === 0} onClick={() => onIndex(index - 1)}><Icon name="chevron-left" />上一张</button><span aria-live="polite">{index + 1} / {images.length}</span><button type="button" className="secondary-button" disabled={index === images.length - 1} onClick={() => onIndex(index + 1)}>下一张<Icon name="chevron-right" /></button></div>
  </Dialog>;
}
