import {Field, SettingsGroup} from './SettingsFields';
import type {KeyboardPreferences} from './keyboard-preferences';

export function KeyboardSettings({values, change, disabled}: {
  values: KeyboardPreferences; change: (key: string, value: any) => void; disabled: boolean;
}) {
  return <>
    <SettingsGroup title="发送消息">
      <Field label="发送快捷键" value={values.send_mode} disabled={disabled} onChange={value => change('send_mode', value)}
        options={[["enter", "Enter"], ["mod-enter", "Ctrl / ⌘ + Enter"]]} hint="只保存在当前浏览器，保存后立即生效。"/>
      <div className="keyboard-shortcut-row"><div><strong>消息换行</strong><p>发送方式改变后，这个组合仍然可用。</p></div><span className="keyboard-keys"><kbd>Shift</kbd><span>+</span><kbd>Enter</kbd></span></div>
      <p className="settings-note">中文输入法选词时，Enter 只确认文字，不会发送。选择 Ctrl / ⌘ + Enter 后，单独按 Enter 也可以换行。</p>
    </SettingsGroup>
    <SettingsGroup title="浏览与返回">
      <div className="keyboard-shortcut-list">
        <div className="keyboard-shortcut-row"><div><strong>关闭当前弹层或返回</strong><p>下拉菜单、图片预览、烹饪专注、右侧详情或移动侧栏打开时使用。</p></div><span className="keyboard-keys"><kbd>Esc</kbd></span></div>
        <div className="keyboard-shortcut-row"><div><strong>移动到下一个或上一个控件</strong><p>在输入框、按钮和菜单之间移动焦点。</p></div><span className="keyboard-keys"><kbd>Tab</kbd><span>/</span><kbd>Shift</kbd><span>+</span><kbd>Tab</kbd></span></div>
        <div className="keyboard-shortcut-row"><div><strong>切换页签或预览图片</strong><p>页签获得焦点时切换分类；图片预览打开时浏览相邻图片。</p></div><span className="keyboard-keys"><kbd>←</kbd><kbd>→</kbd></span></div>
        <div className="keyboard-shortcut-row"><div><strong>浏览设置分类与下拉选项</strong><p>对应控件获得焦点时使用方向键。</p></div><span className="keyboard-keys"><kbd>↑</kbd><kbd>↓</kbd></span></div>
      </div>
    </SettingsGroup>
  </>;
}
