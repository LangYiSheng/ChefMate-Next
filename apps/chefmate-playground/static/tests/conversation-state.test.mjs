import test from 'node:test';
import assert from 'node:assert/strict';
import {moveDrafts,releaseImages,imageMetadata,suggestedReplies,matchesDraft,recoverLocalImages} from '../conversation-state.js';
import {drawStarters,STARTERS} from '../welcome.js';

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

test('random welcome templates are unique and never offer images to a text-only model',()=>{
  for(const random of [()=>0,()=>.4,()=>.99]){
    const templates=drawStarters(false,random);
    assert.equal(templates.length,3);assert.equal(new Set(templates.map(item=>item.id)).size,3);
    assert.equal(templates.some(item=>item.images),false);
  }
  assert.equal(drawStarters(true,()=>.99).length,3);
  for(const template of STARTERS){assert.ok(template.text('豆腐').trim());if(template.blank)assert.ok(template.text('豆腐').includes('豆腐'));}
});
