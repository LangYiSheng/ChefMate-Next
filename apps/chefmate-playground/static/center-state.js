// A save belongs to the editor that started it, even if its request finishes late.
export class CenterEditorState {
  constructor(identity) { this.identity=identity; this.dirty=false; this.busy=false; this.alive=true; this.version=0; }
  change() { if(this.alive && !this.busy) this.dirty=true; }
  begin() { if(!this.alive || this.busy)return null; this.busy=true; return ++this.version; }
  finish(token,{saved=false}={}) {
    if(!this.alive || token!==this.version)return false;
    this.busy=false; if(saved)this.dirty=false; return true;
  }
  dispose() { this.alive=false; this.version++; }
}

export function draftIngredient({name,amount,original,originalAmount}) {
  const sameName=name.value.trim()===original.canonical_name;
  const unchanged=sameName && amount.value.trim()===originalAmount;
  return {name:name.value.trim(),amount:amount.value.trim(),grams:unchanged?original.grams:null,
    amount_evidence:unchanged?original.amount_evidence:'',scalable:original.scalable,
    usda_query:sameName?original.usda_query:'',allergens:sameName?original.allergens:[]};
}
