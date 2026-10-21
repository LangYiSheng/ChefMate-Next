export function moveDrafts(from,to,...maps) {
  if(from===to)return;
  for(const map of maps)if(map.has(from)){map.set(to,map.get(from));map.delete(from);}
}

export function releaseImages(images,revoke=url=>URL.revokeObjectURL(url)) {
  for(const image of images || [])if(image.previewUrl)revoke(image.previewUrl);
}

export function imageMetadata(image) {
  const {blob,previewUrl,local,...metadata}=image;
  return metadata;
}

export function matchesDraft(text,images,input,submittedImages) {
  return String(text || '').trim()===input && images.length===submittedImages.length
    && images.every((image,index)=>image.id===submittedImages[index].id);
}

export function recoverLocalImages(images,makeId=()=>`local:${crypto.randomUUID()}`) {
  return images.map(image=>image.blob ? {...image,id:makeId(),local:true} : image);
}

export function suggestedReplies(messages,enabled=true) {
  if(!enabled)return [];
  const latest=messages?.at(-1);
  if(!latest || latest.role!=='assistant' || latest.streaming || latest.error || latest.cancelled)return [];
  return (Array.isArray(latest.suggested_replies)?latest.suggested_replies:[])
    .filter(item=>typeof item?.title==='string' && item.title.trim() && typeof item.text==='string' && item.text.trim())
    .slice(0,3).map(item=>({title:item.title.trim().slice(0,20),text:item.text.trim().slice(0,8000)}));
}
