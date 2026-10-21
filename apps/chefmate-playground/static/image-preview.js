import { imageURL } from './image-input.js';
import { icon } from './icons.js';

let viewer;
export function openImagePreview(images, index = 0) {
  if (!images.length) return;
  if (!viewer) {
    const dialog = document.createElement('dialog');
    dialog.className = 'image-preview';
    dialog.setAttribute('aria-label', '图片预览');
    const image = document.createElement('img');
    const caption = document.createElement('p'); caption.className = 'image-preview-caption';
    const controls = document.createElement('div'); controls.className = 'image-preview-controls';
    const button = (label, name, action) => {
      const node = document.createElement('button'); node.type = 'button';
      node.className = 'icon-button'; node.setAttribute('aria-label', label);
      node.append(icon(name)); node.addEventListener('click', action); return node;
    };
    const previous = button('上一张图片', 'chevron-left', () => show(viewer.index - 1));
    const next = button('下一张图片', 'chevron-right', () => show(viewer.index + 1));
    const close = button('关闭图片预览', 'x', () => dialog.close());
    controls.append(previous, caption, next, close); dialog.append(image, controls);
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
    dialog.addEventListener('keydown', event => {
      if (event.key === 'ArrowLeft') { event.preventDefault(); show(viewer.index - 1); }
      if (event.key === 'ArrowRight') { event.preventDefault(); show(viewer.index + 1); }
    });
    dialog.addEventListener('close', () => {
      image.removeAttribute('src'); viewer.images = [];
      const opener = viewer.opener;
      const replacement = opener?.dataset.imageId
        ? document.querySelector(`.image-thumbnail[data-image-id="${CSS.escape(opener.dataset.imageId)}"][data-session-id="${CSS.escape(opener.dataset.sessionId)}"]`)
        : null;
      (replacement || (opener?.isConnected ? opener : null) || document.getElementById('composer-input'))?.focus();
    });
    document.body.append(dialog); viewer = { dialog, image, caption, previous, next, images: [], index: 0 };
  }
  viewer.images = images; show(index);
  if (!viewer.dialog.open) { viewer.opener = document.activeElement; viewer.dialog.showModal(); }
}
function show(index) {
  viewer.index = Math.max(0, Math.min(viewer.images.length - 1, index));
  const image = viewer.images[viewer.index];
  viewer.image.src = image.url;
  viewer.image.alt = image.name || '消息图片';
  viewer.caption.textContent = `${viewer.index + 1} / ${viewer.images.length} · ${image.name || '图片'}`;
  viewer.previous.disabled = viewer.index === 0;
  viewer.next.disabled = viewer.index === viewer.images.length - 1;
}
export function imageThumbnail(sessionId, image, images = [image], index = 0) {
  const button = document.createElement('button'); button.type = 'button'; button.className = 'image-thumbnail';
  button.dataset.imageId = image.id; button.dataset.sessionId = sessionId;
  button.setAttribute('aria-label', `预览图片：${image.name || '消息图片'}`);
  const preview = document.createElement('img'); preview.src = imageURL(sessionId, image);
  preview.alt = image.name || '消息图片'; preview.loading = 'lazy';
  button.append(preview);
  button.addEventListener('click', () => openImagePreview(images.map(item => ({ ...item, url: imageURL(sessionId, item) })), index));
  return button;
}
