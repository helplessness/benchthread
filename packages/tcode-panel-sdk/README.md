# TCode 侧栏前端 SDK

用 React、Vue 或原生 JavaScript 写一个侧栏面板。开发界面只需要 Node.js 和 npm。开发服务器提供热更新，SDK 的模拟宿主在浏览器内处理提问、取消和分享。接入真实会话时，同一套界面通过 TCode 注入的接口调用插件自己的 Agent。

## 五分钟启动一个示例

示例使用 Node.js 22。拿到 SDK 源码目录后，选择 [React 示例](examples/react/src/main.jsx) 或 [Vue 示例](examples/vue/src/Assistant.vue)。以下命令从本 README 所在目录执行，两者的启动方式相同。

```bash
cd examples/react     # Vue 使用 cd examples/vue
npm install
npm run dev
```

打开终端打印的本地网址，输入一个问题并点击「提问」。页面先列出运行记录，再显示模拟回答。点击「带入对话」可预览草稿内容；任务执行期间可以取消。刷新页面会清空模拟数据。开发阶段无需启动 Python、TCode 或模型服务。

### 只有 npm 安装包时

SDK 维护者可在本目录运行 `npm pack`，得到可分发的 `tcode-ai-panel-sdk-0.1.0.tgz`，其中包含两个示例的完整源码。拿到文件后，可以在空目录执行以下命令，独立启动示例。将第一行换成下载文件的绝对路径。

```bash
SDK=/absolute/path/tcode-ai-panel-sdk-0.1.0.tgz
npm install --no-save "$SDK"
cp -R node_modules/@tcode-ai/panel-sdk/examples/react my-panel
cd my-panel
npm install "$SDK"
npm run dev
```

Vue 只需将复制来源的 `examples/react` 改为 `examples/vue`。已有前端项目可直接执行 `npm install "$SDK"`。包名为 `@tcode-ai/panel-sdk`，上述流程直接使用本地安装包，无需公共 registry 上已有这个 SDK。

## 侧栏使用哪个模型？

**侧栏跟随当前 TCode 会话的模型。插件无需提供模型选择器，也无需配置 API Key。** 用户在 TCode 中配置好模型服务，加载插件时授予 `team.run`（调用本插件的 Agent 团队）。面板只需调用 `panel.submit({request_id, question})`，宿主负责选择模型并向服务端认证。

主界面确认切换模型后，下一次新提交的侧栏问题立即使用该模型，即使用户还没在主对话发消息。已经提交的任务继续使用提交时绑定的模型。重试同一个 `request_id` 会取回原运行；要用新模型重新提问，请生成新的 UUID。

### 桌面端验证记录

2026-11-22 在 macOS 的 TCode Electron 源码测试实例中，使用已加载并授权的 React 和 Vue 示例，在同一个已有聊天记录的会话中完成以下操作。每次切换后都直接到侧栏提问。表中模型名取自实际发给 DeepSeek 的 HTTP 请求体。

| 主界面操作 | 侧栏 | 请求体中的 `model` | 结果 |
| --- | --- | --- | --- |
| 从 Flash 切换到 Pro | React 问题助手 | `deepseek-v4-pro` | HTTP 200，面板显示回答 |
| 从 Pro 切回 Flash | Vue 问题助手 | `deepseek-v4-flash` | HTTP 200，面板显示回答 |

此行为由 TCode 宿主提供。已有插件可直接使用更新后的宿主，本地模拟模式继续使用开发者配置的 `respond`。

## 在自己的组件里调用

在应用入口选择宿主，再将 `panel` 传给组件。下面使用 Vite 的开发标记，生产构建只启用真实宿主。Vue 示例使用单文件组件并在构建时编译模板，以适配隔离 iframe 的脚本策略。

```js
import {getPanel} from '@tcode-ai/panel-sdk';
import {createMockPanel} from '@tcode-ai/panel-sdk/mock';

const panel = import.meta.env.DEV && !window.tcodePanel
  ? createMockPanel({
      grants: ['team.run', 'selection.share'],
      delayMs: 800,
      respond: turn => '测试回答：' + turn.question,
      onShare: selection => console.log('用户选中的内容', selection.text),
    })
  : getPanel();

const turn = {request_id: crypto.randomUUID(), question: '请解释这段结果'};
const run = await panel.submit(turn);
const {runs, grants} = await panel.history();
```

| API | 用途 |
| --- | --- |
| `getPanel()` | 获取 TCode 当前面板的接口。独立网页使用下行的模拟宿主。 |
| `createMockPanel(options?)` | 创建一个内存会话。可设置授权范围、延迟、自定义同步或异步回答以及分享回调。让 `respond` 抛出异常可测试失败状态。 |
| `panel.submit(turn)` | 提交后返回运行记录。新问题生成新 UUID，同一提交的重试复用原 UUID。带 PDF 或引用来源运行需额外申请 `document.read`。 |
| `panel.history()` | 读取最近 32 条运行和当前权限。任务运行期间刷新此接口；完成后从对应运行的 `result` 取回答。 |
| `panel.cancel(id)` | 取消指定运行，返回更新后的记录。 |
| `panel.share({run, item_id, text})` | 在用户点击时分享已完成运行的选中内容。`run` 是运行 ID，`item_id` 是插件给该条结果的稳定标识。真实宿主写入当前对话草稿，由用户决定何时发送。 |

TypeScript 项目可以 `import type {TCodePanel, PanelRun, PanelTurn} from '@tcode-ai/panel-sdk'`。模拟宿主覆盖界面开发用到的生命周期；模型回答、PDF 解析、授权持久化以及 harness 切换需要在 TCode 中验证。

## 构建并导入 TCode

1. 在选好的示例目录运行 `npm run build`。构建生成 `plugin/ui/index.html`，脚本和样式已内联。同目录下的 `plugin/plugin.json` 声明面板入口和所需权限，`plugin/agent.json` 定义回答问题的 Agent。
2. 将整个 `plugin/` 目录复制到 TCode 当前工作区，例如放入 `my-sidebar/`。使用支持声明式侧栏插件（插件 SDK 1.7）的 TCode，在使用 TCode harness 的会话中选择已配置的模型。
3. 打开「扩展 → 插件 → 安装插件 → 从本地开发目录导入」，输入工作区相对路径 `my-sidebar`，登记并加载。示例申请团队运行和分享选中内容两项权限。打开二级侧栏中的问题助手，提交问题，然后点击「带入对话」检查真实草稿。
4. 修改前端后重新构建，将新的 HTML 同步到已登记目录，关闭再打开面板。修改清单或 Agent 定义后卸载再加载插件。

宿主 iframe 仅启用脚本权限，原生表单提交会被浏览器拦截。示例通过按钮的点击事件调用 `panel.submit()`，开发自己的面板时也可以复用这个写法。

开发网址供浏览器预览。真实宿主从清单的相对路径读取 HTML，入口上限 4 MiB；请把依赖和样式打入文件，避免使用 CDN、外部脚本或运行时模板编译。[完整示例清单](examples/react/plugin/plugin.json)
