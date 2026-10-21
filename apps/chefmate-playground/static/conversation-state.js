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

export const PANTRY_REPLY={title:'已拥有所有小料，帮我勾选掉',text:'当前清单里的调味小料我都已有，请帮我勾选为已备齐；主料保持原来的状态，不确定属于小料的项目先问我。'};

function hasPendingPrep(context) {
  return context?.stage==='prep' && (context.components || []).some(view=>view.kind==='prep_checklist'
    && (view.data?.items || []).some(item=>item.status==='pending'));
}

export function suggestedReplies(messages,enabled=true,context) {
  if(!enabled)return [];
  const latest=messages?.at(-1);
  if(!latest || latest.role!=='assistant' || latest.streaming || latest.error || latest.cancelled)return [];
  let replies=(Array.isArray(latest.suggested_replies)?latest.suggested_replies:[])
    .filter(item=>typeof item?.title==='string' && item.title.trim() && typeof item.text==='string' && item.text.trim())
    .map(item=>({title:item.title.trim().slice(0,20),text:item.text.trim().slice(0,8000)}));
  // Live state replaces the persisted shortcut when checkboxes or stages change after a reply.
  if(context) {
    replies=replies.filter(item=>item.title!==PANTRY_REPLY.title && item.text!==PANTRY_REPLY.text);
    if(hasPendingPrep(context))replies.unshift({...PANTRY_REPLY});
  }
  const titles=new Set(),texts=new Set();
  return replies.filter(item=>{
    if(titles.has(item.title) || texts.has(item.text))return false;
    titles.add(item.title);texts.add(item.text);return true;
  }).slice(0,3);
}
