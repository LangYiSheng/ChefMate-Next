import type { AttachmentPersistence, ImageDraft } from './types';

/** Blobs live in IndexedDB rather than being lost when a tab closes. */
export class BrowserAttachments implements AttachmentPersistence {
  constructor(private readonly accountId: string | null = currentBrowserAccount()) {}
  private databaseName(): string {
    return this.accountId ? `chefmate-react-drafts:${this.accountId}` : 'chefmate-react-drafts';
  }
  private open(): Promise<IDBDatabase> {
    return new Promise((resolve, reject) => {
      if (!globalThis.indexedDB) { reject(new Error('浏览器不支持本地图片草稿。')); return; }
      const request = indexedDB.open(this.databaseName(), 1);
      request.onupgradeneeded = () => request.result.createObjectStore('drafts');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }
  async load(): Promise<Record<string, ImageDraft[]>> {
    const db = await this.open();
    try {
      return await new Promise((resolve, reject) => {
        const transaction = db.transaction('drafts', 'readonly');
        const request = transaction.objectStore('drafts').get('images');
        request.onsuccess = () => resolve(request.result || {});
        request.onerror = () => reject(request.error);
      });
    } finally { db.close(); }
  }
  async save(images: Record<string, ImageDraft[]>): Promise<void> {
    const db = await this.open();
    try {
      await new Promise<void>((resolve, reject) => {
        const transaction = db.transaction('drafts', 'readwrite');
        const clean = Object.fromEntries(Object.entries(images).map(([key, items]) => [key, items.map(({ previewUrl: _previewUrl, ...image }) => image)]));
        transaction.objectStore('drafts').put(clean, 'images');
        transaction.oncomplete = () => resolve();
        transaction.onerror = () => reject(transaction.error);
        transaction.onabort = () => reject(transaction.error);
      });
    } finally { db.close(); }
  }
}

function currentBrowserAccount(): string | null {
  try {return localStorage.getItem('chefmate:browser-account');} catch {return null;}
}

export function releaseImages(images: ImageDraft[] = []): void {
  for (const image of images) if (image.previewUrl?.startsWith('blob:')) URL.revokeObjectURL(image.previewUrl);
}

export function imageMetadata(image: ImageDraft): ImageDraft {
  const { blob: _blob, previewUrl: _previewUrl, local: _local, ...metadata } = image;
  return metadata;
}

export function imageURL(sessionId: string | null, image: ImageDraft): string {
  return image.previewUrl || `/api/sessions/${encodeURIComponent(sessionId || '')}/images/${encodeURIComponent(image.id)}`;
}

/** Image work has no DOM ownership; OffscreenCanvas does encoding off the page. */
export async function prepareImage(file: File): Promise<ImageDraft> {
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) throw new Error('请选择 JPG、PNG 或 WebP 图片。');
  if (file.size > 20_000_000) throw new Error('原图超过20MB，请先裁剪或缩小。');
  const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
  try {
    if (bitmap.width * bitmap.height > 80_000_000) throw new Error('图片尺寸过大，请先裁剪。');
    const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height));
    const width = Math.max(1, Math.round(bitmap.width * scale)), height = Math.max(1, Math.round(bitmap.height * scale));
    const canvas = new OffscreenCanvas(width, height);
    const context = canvas.getContext('2d');
    if (!context) throw new Error('浏览器暂时无法处理这张图片。');
    context.fillStyle = '#fff'; context.fillRect(0, 0, width, height); context.drawImage(bitmap, 0, 0, width, height);
    let blob: Blob | undefined;
    for (const quality of [.86, .78, .68]) {
      blob = await canvas.convertToBlob({ type: 'image/jpeg', quality });
      if (blob.size <= 1_500_000) break;
    }
    if (!blob || blob.size > 1_500_000) throw new Error('图片细节过多，请裁剪后再上传。');
    return { id: `local:${crypto.randomUUID()}`, name: file.name || '粘贴的图片', mime_type: 'image/jpeg', width, height,
      byte_size: blob.size, blob, local: true, previewUrl: URL.createObjectURL(blob) };
  } finally { bitmap.close(); }
}
