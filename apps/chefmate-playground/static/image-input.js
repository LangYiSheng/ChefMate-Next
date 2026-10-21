export const MAX_IMAGES=3;
export function imageURL(sessionId,image) { return image.previewUrl || `/api/sessions/${encodeURIComponent(sessionId)}/images/${encodeURIComponent(image.id)}`; }
async function encodeImage(file) {
  if(!['image/jpeg','image/png','image/webp'].includes(file.type))throw new Error('请选择 JPG、PNG 或 WebP 图片。');
  if(file.size>20_000_000)throw new Error('原图超过20MB，请先裁剪或缩小。');
  const bitmap=await createImageBitmap(file,{imageOrientation:'from-image'});
  try {
    if(bitmap.width*bitmap.height>80_000_000)throw new Error('图片尺寸过大，请先裁剪。');
    const scale=Math.min(1,1600/Math.max(bitmap.width,bitmap.height));
    const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(bitmap.width*scale));canvas.height=Math.max(1,Math.round(bitmap.height*scale));
    const context=canvas.getContext('2d');context.fillStyle='#fff';context.fillRect(0,0,canvas.width,canvas.height);
    context.drawImage(bitmap,0,0,canvas.width,canvas.height);
    let blob;
    for(const quality of [.86,.78,.68]) {
      blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',quality));
      if(blob?.size<=1_500_000)break;
    }
    if(!blob || blob.size>1_500_000)throw new Error('图片细节过多，请裁剪后再上传。');
    return {blob,width:canvas.width,height:canvas.height};
  } finally {bitmap.close();}
}
export async function compressImage(file) { return (await encodeImage(file)).blob; }
export async function prepareImage(file) {
  const {blob,width,height}=await encodeImage(file);
  return {id:`local:${crypto.randomUUID()}`,name:file.name || '粘贴的图片',mime_type:'image/jpeg',width,height,
    byte_size:blob.size,blob,previewUrl:URL.createObjectURL(blob),local:true};
}
