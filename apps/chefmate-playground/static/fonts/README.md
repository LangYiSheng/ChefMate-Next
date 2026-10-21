# ChefMate 本地字体

本目录保留上游字体完整字符集，按浏览器实际使用的字体加载，无运行时第三方字体请求。WOFF2 由原字体仅转换压缩格式，字形、字体名称和可变字重轴保持原样。每个字体的 SIL Open Font License 随文件保留。

| 文件 | 字体 | 来源 |
| --- | --- | --- |
| `noto-sans-sc.woff2` | Noto Sans SC，可变字重 | [Google Fonts](https://github.com/google/fonts/tree/main/ofl/notosanssc) |
| `noto-serif-sc.woff2` | Noto Serif SC，可变字重 | [Google Fonts](https://github.com/google/fonts/tree/main/ofl/notoserifsc) |
| `lxgw-wenkai-tc.woff2` | LXGW WenKai TC，Regular | [Google Fonts](https://github.com/google/fonts/tree/main/ofl/lxgwwenkaitc) |

获取日期：2026-10-22。Noto 字体直接来自上述上游目录的 `NotoSansSC[wght].ttf`（100–900）、`NotoSerifSC[wght].ttf`（200–900），以 FontTools 4.67.0 / Brotli quality 6 转换。LXGW WenKai TC Regular 来自 Google Fonts CSS API 的完整字体（未使用 `text` 子集）。

SHA-256：

```text
27a6f5f09dce5588976b5217b0e2741975c1e28f7991970f839b9e448575703e  noto-sans-sc.woff2
d07b97839716d1e070b1a918b7f0989ef4293208a4591168c81515eb2846d3bd  noto-serif-sc.woff2
76add79445927f04935a811bf680423e9bcf4f67c5694c9512585a91aaa57506  lxgw-wenkai-tc.woff2
```
