# Emerald Rules

## Core Principles
- dream 与 reality 是独立栈
- 长期人格连续性优先于功能丰富度
- 不允许 world_layer 泄漏进现实 identity
- 小 patch 优先于大重构

## Never
- 不要顺手 cleanup
- 不要自动 rename API
- 不要删除 legacy CSS unless confirmed unused
- 不要把 dream prompt 混进 reality stack
- 不要扩大 scope

## Workflow
- grep 后再改
- 先列引用关系
- build before commit
- docs 与实现同步
- 状态机改动必须带 debug log

## Testing
- 区分主墙（physical isolation）与纵深防御
- “assert X 不在 Y” 必须配正样本
- 不允许空库断言伪装成验证
- contract test 优先于 vibe test

## Dream Rules
- v0 只允许定性 body projection
- 他默认不读数字
- reality pipeline 不读取 dream residue
- afterglow 不得包含 world_layer 专有词

## UI Rules
- 先稳定状态机，再做视觉
- 不允许为了视觉重构 dream pipeline
- CSS 清理必须基于 TSX 引用关系