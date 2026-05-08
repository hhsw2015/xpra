# Xpra Mac Client 升级指南

> 官方只发布到 6.4.3 DMG. 6.5+ 通过 "基座 + 源码替换 + CI 编译 .so + patch" 方式构建.

## 构建原理

```
官方 6.4.3 DMG (完整 app bundle: frameworks, GTK, GStreamer, Python 3.11 runtime)
  ↓ 替换 .py (6.5 master 源码)
  ↓ 替换 .so (GitHub Actions CI 编译的 Cython 扩展)
  ↓ 加 nsmenu_thread_fix.dylib (macOS 26 NSMenu 线程 crash 修复)
  ↓ swap GStreamer 1.26 → 1.24.13 (audio regression 修复)
= 6.5-patched Xpra.app
```

**为什么不从源码完整编译**: xpra 的 macOS packaging 脚本 (`packaging/MacOS/`) 需要手动构建整个 GTK/GStreamer/PyGObject 栈 (5-6 小时). 用官方 6.4.3 DMG 作为基座, 只替换 Python 层 + Cython .so, 10 分钟搞定.

**为什么这种替换是安全的**: xpra 的 C 层 (GTK3, GLib, GStreamer, PyGObject binding) 和 Python 层 (.py 业务逻辑 + Cython .so 加速模块) 是清晰分层的. Cython .so 只依赖 Python 3.11 ABI + libpython3.11 + 少量 GLib/GTK C headers (编译时). 只要上游不升级底层 C 库版本 (比如 GTK3→GTK4, Python 3.11→3.12), 替换 Python 层不会破坏兼容性.

**失效条件** (需要重建基座):
- xpra 上游从 Python 3.11 升到 3.12+
- xpra 上游从 GTK3 迁到 GTK4
- Cython .so 开始依赖新版 GLib/GObject API (编译报错时能发现)

## macOS 26 兼容性问题

| 问题 | 原因 | 修复 |
|------|------|------|
| Audio not-negotiated | bundled GStreamer 1.26 appsrc caps regression | swap 为 1.24.13 + compat shim |
| NSMenu crash (6.5+) | libgtkmacintegration 从 worker thread 改 Cocoa menu | nsmenu_thread_fix.dylib swizzle |

## 完整升级流程

### 1. 准备基座 (官方 6.4.3 DMG)

```bash
# 如果 /Applications/Xpra.app 已是 6.4.3 且 GStreamer 已 swap, 可直接用
# 否则从备份恢复:
cd /Applications
sudo curl -fsSL https://github.com/hhsw2015/bin/releases/download/1.0/xpra-arm64-6.4.3-patched.tar.gz | sudo tar -xz
```

### 2. 更新源码到目标版本

```bash
cd ~/Dev/xpra
git fetch origin
git checkout master  # 或目标 tag/commit
git pull
```

### 3. 编译 Cython .so (GitHub Actions)

推送到 GitHub 触发 CI:
```bash
git push origin master
# CI workflow: .github/workflows/macos-build.yml
# 产物: xpra-macos-arm64-patch.tar.gz (所有 .so + .py)
```

或手动触发: GitHub repo → Actions → "macOS arm64 Build" → Run workflow

下载 artifact 后解压:
```bash
# 从 GitHub Actions 下载 xpra-macos-arm64-patch.tar.gz
tar -xzf xpra-macos-arm64-patch.tar.gz
# 得到 xpra/ 目录 (包含 .py + .so)
```

### 4. 替换 Python 源码 + .so 到 app bundle

```bash
APP=/Applications/Xpra.app/Contents/Resources/lib/python3.11
# 如果 app bundle 用的是 python/ 而不是 python3.11/:
# APP=/Applications/Xpra.app/Contents/Resources/lib/python

# 全量替换 xpra 模块 (保留 app bundle 其他文件):
sudo rm -rf "$APP/xpra"
sudo cp -R xpra/ "$APP/xpra"

# 清 bytecode cache:
sudo find "$APP/xpra" -name "*.pyc" -delete
sudo find "$APP/xpra" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
```

### 5. 编译并安装 nsmenu_thread_fix.dylib

```bash
cd ~/Dev/dvina-2api/tools

# 必须 arm64e (macOS 26 Xpra 进程是 arm64e)
clang -dynamiclib -framework Foundation -framework AppKit \
  -o nsmenu_thread_fix.dylib nsmenu_thread_fix.m \
  -arch arm64e -install_name @rpath/nsmenu_thread_fix.dylib

file nsmenu_thread_fix.dylib
# 确认: Mach-O 64-bit dynamically linked shared library arm64e

sudo cp nsmenu_thread_fix.dylib /Applications/Xpra.app/Contents/Resources/lib/
```

### 6. GStreamer swap (仅首次建基座时)

GStreamer swap 只在首次从官方 6.4.3 DMG 创建基座时做一次. 之后升级 (替换 .py/.so) 不影响已 swap 的 GStreamer dylibs, 因为它们在 `lib/` 而非 `lib/python*/xpra/`.

```bash
# 验证基座 GStreamer 版本:
strings /Applications/Xpra.app/Contents/Resources/lib/libgstreamer-1.0.0.dylib | grep -E "^1\.[0-9]+\.[0-9]+"
# 应显示 1.24.x (说明已 swap, 跳过)
# 如果显示 1.26.x → 需要首次 swap:
#   安装 GStreamer.framework 1.24.x: https://gstreamer.freedesktop.org/download/
#   bash ~/Dev/dvina-2api/tools/swap_xpra_gstreamer.sh
```

### 7. 编译 libgst_compat_shim.dylib (如需)

仅当 GStreamer 被 swap 后, app 内的 `_gi_gst.cpython-311-darwin.so` 引用了 1.26-only 符号时需要.

```bash
cat > /tmp/gst_compat_shim.c << 'EOF'
#include <stdbool.h>
bool gst_structure_is_writable(void *s) { return true; }
EOF

clang -dynamiclib -o libgst_compat_shim.dylib /tmp/gst_compat_shim.c \
  -arch arm64 -arch arm64e -arch x86_64 \
  -install_name @rpath/libgst_compat_shim.dylib

sudo cp libgst_compat_shim.dylib /Applications/Xpra.app/Contents/Resources/lib/
```

### 8. 验证

```bash
# 快速检查 (不连接):
~/Dev/dvina-2api/tools/xpra_attach.sh --version
# 应输出: xpra vX.Y.Z 无 crash

# 完整测试:
chisel xpra attach
# 确认: 有画面 + 有声音 + 不 crash
```

### 9. 打包备份上传

```bash
cd /Applications
VER=$(~/Dev/dvina-2api/tools/xpra_attach.sh --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
tar czf ~/xpra-arm64-${VER}-patched.tar.gz Xpra.app/

# 上传:
gh release upload 1.0 ~/xpra-arm64-${VER}-patched.tar.gz --repo hhsw2015/bin --clobber
```

### 10. 更新文档

- `docs/skywork-runbook.md`: 更新下载链接
- `chisel.sh` (xpra setup section): 更新 URL
- `docs/known-issues.md`: 如有新问题追加

## xpra_attach.sh 工作原理

```bash
# ~/Dev/dvina-2api/tools/xpra_attach.sh
# 自动检测 app bundle 中存在的 shim, 构造 DYLD_INSERT_LIBRARIES:
#   nsmenu_thread_fix.dylib (如存在) → 修 NSMenu thread crash
#   libgst_compat_shim.dylib (如存在) → 修 GStreamer 1.26 missing symbol
# 两者可共存, 不存在的自动跳过. 新旧版本都兼容.
```

## GitHub Actions CI (.github/workflows/macos-build.yml)

在 macOS 14 arm64 runner 上编译 Cython .so:
- 安装 brew 依赖 (gtk+3, gstreamer, etc.)
- `python setup.py build_ext --inplace`
- 打包所有 .py + .so 为 artifact

触发条件: push to master, 或 workflow_dispatch 手动触发.

## 什么时候可以去掉 shim

- **nsmenu_thread_fix**: xpra 上游修了 worker thread 菜单操作
  - 验证: 不注入 dylib 直接跑, 连接后不 crash = 已修复
- **libgst_compat_shim**: 新版 Xpra 自带 GStreamer 1.24, 或官方 DMG 更新到 6.5+
  - 验证: `strings .../libgstreamer-1.0.0.dylib` 显示 1.24 = 不需要
- **整个流程**: 当官方发布 macOS 26 兼容的 6.5+ DMG 时, 直接装 DMG 即可

## 故障排查

| 症状 | 原因 | 修复 |
|------|------|------|
| `incompatible architecture (have arm64, need arm64e)` | dylib 编译时没加 `-arch arm64e` | 重新编译 |
| `could not get a reference to type class` | 删了 libgtkmacintegration | 恢复它, 用 swizzle 方案 |
| `gtk_main_quit: assertion failed` | XPRA_OSX_SHOW_MENU_DEFAULT=0 | 不要设这个 env var |
| Audio not-negotiated | GStreamer 1.26 未被替换 | 跑 swap_xpra_gstreamer.sh |
| PulseAudio conflict (server) | 残留 PA 实例 | `chisel xpra fix-audio` 或 `cct enable-xpra` |
| .so import error after upgrade | CI .so 和 app bundle Python 版本不匹配 | 确认都是 cpython-311 |
| 没画面 | chromium 被杀或 Xvfb 死了 | `cct enable-xpra` 重启 server |
