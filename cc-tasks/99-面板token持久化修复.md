# Brief 99 · 管理面板 token 持久化修复

> 背景:release 复测(20260719),每次重新进入管理面板都要重输 token。
> 审计发现持久化机制**已存在**(`localStorage['qq_admin_key']`,
> index.html:2160 读、2522 写),问题是恢复链路坏了。独立小单,无依赖。

## 1. 🔴 已定位的真 bug

- `admin/static/index.html:3063`:
  `'Authorization': Bearer ${localStorage}` ——拼的是整个 Storage 对象,
  实际发出 `Bearer [object Storage]`,该请求必 401。改为
  `localStorage.getItem('qq_admin_key')`(与 3083 行写法对齐)。
- 顺手全文件 grep `${localStorage`,确认无同类笔误。

## 2. 🟡 诊断并修复「每次都要重输」

- 复现并回答:存了 token 的情况下刷新/重开面板,为什么仍出现输入框?
  排查方向:①登录层是否无条件弹出、没先试 stored token;②stored token
  校验请求走的是不是 §1 的坏代码路径,401 后误判为未登录;③token 输入
  提交时是否某些入口没写回 localStorage。
- 目标行为:打开面板 → 有 stored token 就静默用它发一次校验请求 →
  通过直接进面板;401 才弹输入框(带「token 已失效,请重新输入」提示)。
- 加「退出登录」按钮(清 localStorage 回登录态),放设置/右上角,补齐闭环。

## 验收

- 输一次 token → 关浏览器重开/刷新,直接进面板不再询问。
- 填错 token 有明确提示;退出登录后回到输入框。
- 全文件无 `${localStorage` 裸拼;既有页面功能抽查零回归。
