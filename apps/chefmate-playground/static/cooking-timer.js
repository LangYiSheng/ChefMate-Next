import {clockText, remainingSeconds} from './ui-protocol.js';

export function durationText(value) {
  if (!Number.isFinite(value) || value < 0) return '时长未知';
  const total = Math.round(value), hours = Math.floor(total / 3600);
  const minutes = Math.floor(total % 3600 / 60), seconds = total % 60;
  return [hours ? `${hours} 小时` : '', minutes ? `${minutes} 分钟` : '',
    seconds || !total ? `${seconds} 秒` : ''].filter(Boolean).join(' ');
}

export function timerPresentation(timer, now = Date.now()) {
  const remaining = remainingSeconds(timer, now);
  const expired = timer.status === 'finished' || timer.status === 'running' && remaining === 0;
  const state = expired ? 'expired' : ['running', 'paused', 'cancelled'].includes(timer.status) ? timer.status : 'unknown';
  const status = ({expired:'时间到', running:'正在计时', paused:'已暂停', cancelled:'已结束', unknown:'等待同步'})[state];
  const progress = Number.isFinite(timer.seconds) && timer.seconds > 0 && remaining != null
    ? Math.min(1, Math.max(0, remaining / timer.seconds)) : 0;
  return {remaining, clock:remaining == null ? '--:--' : clockText(remaining), status, state, expired, progress};
}

export function timerForStep(timers, recipeId, stepId) {
  const matches = (timers || []).filter(timer => timer.recipe_id === recipeId && timer.step_id === stepId);
  return matches.find(timer => ['running', 'paused'].includes(timer.status)) || matches.at(-1) || null;
}

export function manualTimerSeconds(minutes, seconds) {
  if ((minutes === '' || minutes == null) && (seconds === '' || seconds == null)) return null;
  const m = Number(minutes || 0), s = Number(seconds || 0);
  if (!Number.isInteger(m) || !Number.isInteger(s) || m < 0 || s < 0 || s > 59) return null;
  const total = m * 60 + s;
  return total >= 1 && total <= 86400 ? total : null;
}

export function timerDraftParts(minutes) {
  const total = Number.isFinite(minutes) && minutes >= 0 ? Math.round(minutes * 60) : 180;
  return {minutes:Math.floor(total / 60), seconds:total % 60};
}
