// Cursor pages remain authoritative; stale refreshes cannot replace a newer list.
export class SessionFeed {
  constructor(fetchPage,{onChange=()=>{}}={}){this.fetchPage=fetchPage;this.onChange=onChange;this.items=[];this.cursor=null;this.loading=false;this.error=null;this.epoch=0;}
  async load({more=false}={}){
    if(more && (this.loading || !this.cursor))return;
    const epoch=++this.epoch,target=more?20:Math.max(20,this.items.length);
    let cursor=more?this.cursor:null,items=more?[...this.items]:[],received=0;
    this.loading=true;this.error=null;this.onChange();
    try{
      do{
        const page=await this.fetchPage({limit:20,cursor});
        if(epoch!==this.epoch)return;
        const byId=new Map(items.map(item=>[item.id,item]));
        for(const item of page.items || [])byId.set(item.id,item);
        items=[...byId.values()];received+=(page.items || []).length;
        const next=page.next_cursor || null;
        if(next && next===cursor)throw new Error('会话列表读取失败，请重试。');
        cursor=next;
      }while(!more && cursor && received<target);
      this.items=items;this.cursor=cursor;
    }catch(error){if(epoch===this.epoch){this.error=error;throw error;}}
    finally{if(epoch===this.epoch){this.loading=false;this.onChange();}}
  }
}
