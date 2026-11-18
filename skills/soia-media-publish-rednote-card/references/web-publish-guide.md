# 小红书网页端发布与长文实操参考

> 本参考承接 [SKILL.md](../SKILL.md) 的「写长文」平台规格与网页端传图/并发/验收细则。

## 长文形态细则（正文 >1000 字时）

小红书图文笔记上限 1000 字，超限时严禁硬删为干瘪摘要，按两路线二选一：

### 路线一（首选 · 小红书端）：官方「写长文」

以下平台规格由客户真实发布验证：

| 环节 | 实测结论 |
|---|---|
| 入口 | 创作服务平台 → 发布笔记 → 第三个 tab「写长文」（URL `target=article`）；支持「新的创作」或「导入链接」 |
| 编辑器 | 富文本：H1/H2、有序/无序列表、引用、划线、插图、表情；实时字数与自动保存 |
| 一键排版 | 粘贴全文后点「一键排版」自动分页成文字卡（约 1180 字 → 5 张，可逐张预览） |
| 模板族 | 清晰明朗（黑/白/米）、黑白极简、理性现代、素雅底纹（粉/米/绿/蓝）、文艺清新（可自动生成插画封面）、平实叙事 |
| 字数徽章 | 「全文 N 字｜阅读需 X 分钟」平台自动生成，无需自己绘制 |
| 封面 / 标题 / 描述 | 「封面设置」单独调；长文标题上限 20 字；另有 ≤1000 字笔记描述框放钩子+导读+互动问题（不复制长文开头） |
| 长文合集 | 名称 ≤20、简介 ≤100、封面必填（png/jpg ≤5M，推荐 3:4）；同一合集模板保持一致 |
| 组件与草稿 | 支持挂 Red Skill 组件与关联活动；「暂存离开」入草稿箱，平台自动保存 |

### 路线二（跨平台复用时）：自产文字卡

仅当同一长文还需复用到公众号 / X 或维持系列统一视觉时使用：

- **全文逐字上卡**：按知识单元拆成 4–8 张文字卡（封面 + 内容 + 结语卡），图片承载全文；正文放钩子、导读与互动问题。
- **封面字数徽章**：「全文约 N 字｜阅读需 X 分钟」，按原稿实数计算，不虚标。
- **出图路由**：交给 `soia-media-generate-article-image`，先定 `00-series-bible` 基座再逐卡编译 Prompt 落盘生图；上卡只修正明显坏损标点，不改原文措辞。

## 浏览器传图方案与实测排除项

宿主文件上传工具不可用时的方案对比（**前四项实测不可行，勿重复踩坑**）：

| 方案 | 结论与原因 |
|---|---|
| 本地 HTTP server + 页面 `fetch` | ❌ Chrome PNA 策略拦截 HTTPS 访问 localhost，请求发不出 |
| base64 分片注入 | ❌ 高清图编码载荷过重（3 张图 ~1.29M 字符） |
| 合成键盘事件 `Cmd+V` | ❌ 扩展合成键不携带系统剪贴板图像载荷 |
| `osascript` 盲打键盘操作原生文件框 | ❌ **禁用**：会误击客户当前聚焦的其它 Chrome 窗口且挂起 tab JS |
| **SVG `foreignObject` + `canvas`** | ✅ 拥有 `html_render` HTML 源码时的首选（KB 级注入，2x 清晰度） |
| **系统剪贴板 + Clipboard API** | ✅ 纯位图（imagegen 直出 PNG/JPG 等）标准方案 |

### 方案 A：SVG `foreignObject` + `canvas`（有 HTML 源码）

```javascript
// 先把结果写进 window.__r 再单独读取，兼容不 await 异步返回值的宿主 JS 工具
const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="2160" height="2880">'
  + '<foreignObject width="100%" height="100%">'
  + '<div xmlns="http://www.w3.org/1999/xhtml" style="transform:scale(2);transform-origin:0 0;width:1080px;height:1440px">'
  + '<style>' + css + '</style>' + bodyHtml + '</div></foreignObject></svg>';
const img = new Image();
img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
await img.decode();

const c = document.createElement('canvas'); c.width = 2160; c.height = 2880;
c.getContext('2d').drawImage(img, 0, 0, 2160, 2880);
const blob = await new Promise(r => c.toBlob(r, 'image/png'));

const dt = new DataTransfer();
dt.items.add(new File([blob], '1-cover.png', {type: 'image/png'}));
const input = document.querySelector('input[type=file]');
input.files = dt.files;
input.dispatchEvent(new Event('change', {bubbles: true}));
```

### 方案 B：系统剪贴板 + Clipboard API（纯位图）

```bash
# JPEG 可先压至 ~350KB；仅写剪贴板，不发任何键盘事件
osascript -e 'set the clipboard to (read (POSIX file "/abs/path/N.jpg") as JPEG picture)'
```

```javascript
// 标签页须处于聚焦状态，否则 navigator.clipboard.read() 报 NotAllowedError
const items = await navigator.clipboard.read();
for (const it of items) for (const t of it.types) if (t.startsWith('image/')) {
  const b = await it.getType(t);
  window.__files = window.__files || [];
  window.__files[N] = new File([b], 'N.png', {type: t});
}
// 全部就绪后一次注入 multiple file input
const dt = new DataTransfer();
window.__files.filter(Boolean).forEach(f => dt.items.add(f));
const input = [...document.querySelectorAll('input[type=file]')].find(i => i.multiple && /png/.test(i.accept));
input.files = dt.files;
input.dispatchEvent(new Event('change', {bubbles: true}));
```

**并发防护与逐张核验**：写入前确认无并行改动剪贴板的自动化任务（如 `pgrep -f 'codex exec'` 为空）；读回 `File` 后用 `createImageBitmap` 比对宽高与源文件一致再注入，不符立即重写入重读。替换旧图采用「先传新、后删旧」（悬停缩略图点右上 ×，网格左移后重复同一位置）。

## 标签、原创声明与发布后验收

- **话题标签**：严禁连打 `#A #B #C`（前几项会退化为黑字纯文本），必须逐个输入 `#关键词` → 等下拉 → 点选（可参考浏览量择优），逐个核实编辑器内为**蓝色**。
- **原创声明**：开启会弹出《原创声明须知》协议条款，必须单独征得客户确认才可勾选；未表态保持关闭。
- **正式发布核实**：URL 带 `published=true` 与控制台提示均不等于已对外生效（草稿箱可能留自动存档残留）；必须前往**笔记管理页**及正式对外链接/搜索入口核实条目真实可见，审核中则如实标注「已提交待审核」。
