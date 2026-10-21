import test from 'node:test';
import assert from 'node:assert/strict';
import { readAppearance, resolveTheme } from '../appearance.js';
import { readKitchenPreferences, saveKitchenPreferences, TimerSound } from '../timer-sound.js';

const storage = initial => ({ value:initial, getItem(){return this.value;},setItem(key,value){this.value=value;} });
test('appearance follows system only when requested and repairs invalid browser preferences', () => {
  assert.equal(resolveTheme('system',true),'dark');
  assert.equal(resolveTheme('system',false),'light');
  assert.equal(resolveTheme('light',true),'light');
  assert.equal(resolveTheme('dark',false),'dark');
  assert.equal(readAppearance(storage('{"theme":"invalid","density":"compact"}')).theme,'light');
  assert.equal(readAppearance(storage('broken')).theme,'light');
});

test('timer sound starts enabled and persists a browser-only mute', () => {
  const saved = storage(null);
  assert.equal(readKitchenPreferences(saved).timer_sound,true);
  saveKitchenPreferences({timer_sound:false},saved);
  assert.equal(readKitchenPreferences(saved).timer_sound,false);
});

function fakeAudio() {
  const calls = [];
  class Audio {
    constructor(){ this.state='running';this.currentTime=10;this.destination={}; }
    createOscillator(){ const oscillator = {type:'',frequency:{value:0}, connect(){},disconnect(){},start(at){calls.push({frequency:this.frequency.value,at});},stop(){}};return oscillator; }
    createGain(){return {gain:{setValueAtTime(){},linearRampToValueAtTime(){},exponentialRampToValueAtTime(){}},connect(){},disconnect(){}};}
  }
  return { scope:{AudioContext:Audio},calls };
}
test('muted timers never start audio, but the explicit preview can play', async () => {
  const {scope,calls}=fakeAudio(), sound=new TimerSound(scope,()=>false);
  assert.equal(await sound.play(),false); assert.equal(sound.context,null);
  assert.equal(await sound.play({preview:true}),true);
  assert.equal(calls.length,6);
  assert.deepEqual(calls.map(call=>call.frequency),[660,880,660,880,660,880]);
});

test('timer chime respects a mute applied while browser audio is resuming', async () => {
  const {scope,calls}=fakeAudio();let enabled=true, resume;
  const sound=new TimerSound(scope,()=>enabled);
  sound.context=new scope.AudioContext();sound.context.state='suspended';
  sound.context.resume=()=>new Promise(resolve=>{resume=()=>{sound.context.state='running';resolve();};});
  const playing=sound.play();enabled=false;resume();
  assert.equal(await playing,false);assert.equal(calls.length,0);
  assert.equal(await new TimerSound({}).play(),false);
});
