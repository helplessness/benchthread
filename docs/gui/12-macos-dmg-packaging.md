# macOS 桌面打包

在 Apple Silicon Mac 上，准备 Python 3.12+、Node.js 22.12+、npm 和 `uv`。从干净的仓库根目录运行（不需要自己的模型、账号配置或相邻 TMCP 仓库）：

```bash
python3 -m venv .desktop-venv
uv pip install --python .desktop-venv/bin/python -e '.[desktop]' -r packaging/requirements-macos.txt
npm --prefix web ci
npm --prefix desktop ci
npm --prefix desktop test
npm --prefix desktop run package:mac
npm --prefix desktop run smoke:backend:mac
```

后端位于 `build/release/backend/`，安装镜像位于 `desktop/release/`。PyInstaller 明确排除 `tmcp`，只内置 TCode 的通用 Job Protocol。安装包自带 Electron Chromium、冻结 Python 后端，以及用于 Computer Use 的 Cua CLI 和 Node SDK；收件人不需要另装 Python、Node.js、Docker 或驱动。电脑控制默认关闭，开启后仍须按 macOS 提示授权。

打包会先校验 Cua 归档和 CLI。用 `hdiutil verify` 检查镜像后，挂载或复制到独立目录，确认 `TCode.app/Contents/Resources/computer/` 下有可执行的 `cua-driver`、`LICENSE`、`THIRD_PARTY_NOTICES.md` 和 `MPL-2.0.txt`，且 `Resources/app.asar.unpacked/node_modules/@trycua/` 有 SDK 和 darwin-arm64 原生运行库。再分别验证应用启动/退出、内置网页浏览器和授权后的电脑控制；镜像校验及后端 smoke 都不替代真实桌面验收。

冻结后端只随包附带 `.tcode/skills/` 下的 `anatomy`、`docx` 和 `pdf`，包括各自许可证。随包附带的是技能文件；LibreOffice、Pandoc、Poppler、Tesseract 等可选工具须按需要在执行环境另行安装。当前命令生成的是未做 Developer ID 签名和公证的本地测试包，不应当作下载即用的发行包。只想运行源码版，请按[桌面首次启动](../../README.md#macos启动桌面端)操作。
