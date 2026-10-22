# ChefMate 本地图标

从官方 `lucide-static` 1.60.0 中选取 SVG，合成为本地 sprite；保留原始路径数据。聊天、设置和厨房组件共用同一套图标。

## 来源与许可证

- [上游项目](https://github.com/lucide-icons/lucide)
- [原始软件包](https://registry.npmjs.org/lucide-static/-/lucide-static-1.60.0.tgz)
- ISC 许可证，完整上游声明保留在 [LICENSE](LICENSE)。

## 使用约定

[`icons.js`](../../icons.js)提供统一装饰图标函数。只有图标的按钮必须有可访问名称，装饰图标对辅助技术隐藏。运行时无需 CDN 或 npm 包。

新增图标同步白名单、sprite 和来源记录。配色与交互规则见[视觉规范](../../../../../docs/design/ui.md)。
