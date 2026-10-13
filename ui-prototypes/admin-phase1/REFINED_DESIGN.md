# 选定方向：C 布局 × A/B 日夜配色

打开 `refined.html`。顶部切换日间、夜间，也可以对比书卷衬线、标题衬线和简洁黑体。首次打开跟随系统明暗偏好；切换时保留输入、折叠与查询状态。

## 布局和颜色

沿用 C 的紧凑统计条、三列表单、独立功能卡和侧栏。为衬线体增加少量文字呼吸空间：正文 15px / 1.7、说明 13px、标题 28px，控件 38px，卡片内边距 20px。字段间距 16px，模块间距 12px；不拉大中文字符间距。

日间沿用 B 的灰紫底色和紫灰主操作；夜间沿用 A 的深灰底色和柔和灰绿主操作。白天卡片用近白实色，让小字号宋体更清晰。语义颜色、按钮、焦点、卡片与反馈都随模式变化。

侧栏显示当前配置和口味摘录。页面只保留字段说明与操作反馈。交互预览保留模拟失败、填入示例和重置，以便对照完整状态。重置操作保留确认，这是避免误清空输入的实际交互。

## 字体选择

默认组合为 Lora（拉丁字母、数字）+ Noto Serif SC（中文）。这是接近柔和阅读感的开源替代方案，不声称复刻 Claude 的字体。

- [Lora 上游](https://github.com/cyrealtype/Lora-Cyrillic)：柔和的文字衬线体；[Google Fonts 分发源](https://github.com/google/fonts/tree/main/ofl/lora)提供可变字体和 OFL 许可。
- [Noto Serif CJK 上游](https://github.com/notofonts/noto-cjk/tree/main/Serif)：简体中文选择 SC 区域版，与思源宋体同源；本地使用 200–900 字重可变字体。
- [霞鹜文楷](https://github.com/lxgw/LxgwWenKai)：也属开源，但偏手写楷体，此次没有选作默认管理面字体。

两款默认字体均随样机保存在 `fonts/`，附各自 OFL 许可。完整区域字体转换为 WOFF2，无字形裁剪，不依赖在线 CDN 或系统预装字体。转换工具只用于制作资源，不是样机运行依赖。

三种字体展示方式：

| 模式 | 效果 |
| --- | --- |
| 书卷衬线 | 标题、正文、表单、结果均用 Lora + Noto Serif SC；默认 |
| 标题衬线 | 标题与口味摘录用衬线，其余用中文系统黑体 |
| 简洁黑体 | 全部用中文系统黑体，作为密度和读感对照 |

字体二进制来自官方公开分发地址：

- `https://raw.githubusercontent.com/google/fonts/main/ofl/lora/Lora%5Bwght%5D.ttf`
- `https://raw.githubusercontent.com/notofonts/noto-cjk/main/Serif/Variable/OTF/Subset/NotoSerifSC-VF.otf`

许可原文分别来自对应仓库的 `ofl/lora/OFL.txt` 与 `Serif/LICENSE`。资源只在独立样机中使用，未安装到系统或接入生产管理面。

## 本轮验收

日夜切换、字体切换和表单状态检查见 `REFINED_VALIDATION.md`。浏览器真实渲染仍需预览确认；上一轮 file URL 被浏览器工具拒绝，本轮没有绕过该限制。

完成本轮样机后等待确认，不推进生产接入或功能分类迁移。旧 A/B/C 保留为历史对照。
