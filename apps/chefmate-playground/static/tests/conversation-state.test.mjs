import test from 'node:test';
import assert from 'node:assert/strict';
import {moveDrafts,releaseImages,imageMetadata,suggestedReplies,matchesDraft,recoverLocalImages,PANTRY_REPLY} from '../conversation-state.js';
import {drawStarters,STARTERS} from '../welcome.js';

test('new starters cover personal recipes, journals and memory without offering unavailable images',()=>{
  for(const category of ['kitchen','recipes','journal','memory']){
    const selected=drawStarters(false,()=>.5,category);
    assert.ok(selected.length>=2);assert.ok(selected.every(item=>!item.images && (item.category || 'kitchen')===category));
  }
});

test('first-send draft migration retains attachments and does not touch another conversation',()=>{
  const image={id:'local:photo',blob:{size:20},previewUrl:'blob:preview',local:true};
  const text=new Map([['draft:one','今晚吃什么'],['other','另一段草稿']]);
  const images=new Map([['draft:one',[image]],['other',[]]]);
  moveDrafts('draft:one','created-id',text,images);
  assert.equal(text.has('draft:one'),false);
  assert.equal(text.get('created-id'),'今晚吃什么');
  assert.equal(images.get('created-id')[0],image);
  assert.equal(text.get('other'),'另一段草稿');
  // A failed later upload leaves the migrated draft intact for retry.
  moveDrafts('draft:missing','created-id',text,images);
  assert.equal(images.get('created-id')[0],image);
});

test('accepted image metadata has no local Blob or URL, and cleanup only revokes local previews',()=>{
  const image={id:'uploaded',name:'photo',blob:{size:20},previewUrl:'blob:preview',local:false,width:20};
  assert.deepEqual(imageMetadata(image),{id:'uploaded',name:'photo',width:20});
  const revoked=[];releaseImages([image,{id:'remote'}],url=>revoked.push(url));
  assert.deepEqual(revoked,['blob:preview']);
});

test('an accepted retry consumes only the exact retained text and attachments',()=>{
  const submitted=[{id:'image:one'},{id:'image:two'}];
  assert.equal(matchesDraft('  帮我看看  ',submitted,'帮我看看',submitted),true);
  assert.equal(matchesDraft('后来写的新问题',submitted,'帮我看看',submitted),false);
  assert.equal(matchesDraft('帮我看看',[{id:'later-photo'},{id:'image:two'}],'帮我看看',submitted),false);
  assert.equal(matchesDraft('帮我看看',[],'帮我看看',submitted),false);
});

test('a failed first-send upload returns local attachments without retaining the old session IDs',()=>{
  const blob={size:20};
  const images=recoverLocalImages([{id:'old-session-image',local:false,blob,previewUrl:'blob:preview'}],()=> 'local:retry');
  assert.equal(images[0].id,'local:retry');assert.equal(images[0].local,true);
  assert.equal(images[0].blob,blob);assert.equal(images[0].previewUrl,'blob:preview');
  const texts=new Map([['created-id','保留的问题'],['current-session','用户正在写的新内容']]);
  const attachments=new Map([['created-id',images]]);
  moveDrafts('created-id','recoverable-draft',texts,attachments);
  assert.equal(texts.get('recoverable-draft'),'保留的问题');
  assert.equal(texts.get('current-session'),'用户正在写的新内容');
  assert.equal(attachments.has('created-id'),false);
});

test('suggested replies belong only to the latest completed successful answer and respect setting',()=>{
  const answer={role:'assistant',suggested_replies:[{title:' 换一种做法 ',text:' 请换成蒸的。 '},{title:'',text:'invalid'},null,{title:'采购',text:'列出采购清单'},{title:'备菜',text:'开始备菜'},{title:'多余',text:'fourth'}]};
  assert.deepEqual(suggestedReplies([answer]),[{title:'换一种做法',text:'请换成蒸的。'},{title:'采购',text:'列出采购清单'},{title:'备菜',text:'开始备菜'}]);
  assert.deepEqual(suggestedReplies([answer,{role:'user',content:'新问题'}]),[]);
  for(const flag of ['streaming','error','cancelled'])assert.deepEqual(suggestedReplies([{...answer,[flag]:true}]),[]);
  assert.deepEqual(suggestedReplies([answer],false),[]);
});

test('the pantry shortcut uses live preparation state and stays an editable user request',()=>{
  const answer={role:'assistant',suggested_replies:[{title:'看看清单',text:'看看清单'},{...PANTRY_REPLY},{title:'看看清单',text:'看看清单'}]};
  const context={stage:'prep',components:[{kind:'prep_checklist',data:{items:[{id:'salt',status:'pending'},{id:'egg',status:'pending'}]}}]};
  const replies=suggestedReplies([answer],true,context);
  assert.equal(replies[0].title,'已拥有所有小料，帮我勾选掉');
  assert.ok(replies[0].text.includes('主料保持原来的状态'));
  assert.equal(replies.filter(item=>item.title===PANTRY_REPLY.title).length,1);
  assert.equal(replies.filter(item=>item.title==='看看清单').length,1);
  assert.equal(context.components[0].data.items.every(item=>item.status==='pending'),true);
  assert.deepEqual(suggestedReplies([answer],false,context),[]);
  assert.deepEqual(suggestedReplies([answer,{role:'user',content:PANTRY_REPLY.text}],true,context),[]);
  context.components[0].data.items.forEach(item=>item.status='ready');
  assert.equal(suggestedReplies([answer],true,context).some(item=>item.title===PANTRY_REPLY.title),false);
  context.components[0].data.items[0].status='pending';context.stage='cooking';
  assert.equal(suggestedReplies([answer],true,context).some(item=>item.title===PANTRY_REPLY.title),false);
});

test('reply settings preserve up to six choices and default to three',()=>{
  const messages=[{role:'assistant',suggested_replies:Array.from({length:6},(_,i)=>({title:`建议${i}`,text:`话题${i}`}))}];
  for(const count of [1,3,6])assert.equal(suggestedReplies(messages,true,undefined,count).length,count);
  assert.equal(suggestedReplies(messages).length,3);
  assert.equal(suggestedReplies(messages,true,undefined,'6').length,3);
  assert.equal(suggestedReplies(messages,false,undefined,6).length,0);
  const prep={stage:'prep',components:[{kind:'prep_checklist',data:{items:[{status:'pending'}]}}]};
  assert.deepEqual(suggestedReplies(messages,true,prep,1),[PANTRY_REPLY]);
});

test('random welcome templates are unique and never offer images to a text-only model',()=>{
  for(const random of [()=>0,()=>.4,()=>.99]){
    const templates=drawStarters(false,random);
    assert.equal(templates.length,3);assert.equal(new Set(templates.map(item=>item.id)).size,3);
    assert.equal(templates.some(item=>item.images),false);
  }
  assert.equal(drawStarters(true,()=>.99).length,3);
  for(const template of STARTERS){assert.ok(template.text('豆腐').trim());if(template.blank)assert.ok(template.text('豆腐').includes('豆腐'));}
});
