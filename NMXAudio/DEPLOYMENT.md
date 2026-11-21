# Intel macOS Tahoe 安装与回退

本指南针对 Release 的 Tahoe 主机模板、Intel macOS 26 和 PCI HDA 控制器。实机验证环境为 macOS 26.7.4（25G422）、ASUS TUF Gaming Z690-Plus WiFi D4、ALC897 布局 14 和 RTX 3080 DisplayPort。板载输入输出、本地化、可选 HAL 加载及 DP 拔线后自动移除音频输出已在该环境验证。其他板卡、布局和显示器需单独复核。

## 1. 组装完整安装包

准备 Python 3 和 Xcode Command Line Tools。在 GitHub Release 下载 ZIP 和 `SHA256SUMS`，先执行 `shasum -a 256 -c SHA256SUMS`，再解压 ZIP。从你已有的原版 macOS 系统备份或 Apple 安装介质取得完整、仍有 Apple 签名的 Intel `AppleHDA.kext`。本次验证使用 AppleHDA **600.2**，包含五个内核插件和 `AppleHDAHALPlugIn.bundle`。不同 AppleHDA 版本需要重新做内核集合预检和实机验证。

在解压后的目录里运行下面的命令。把 AppleHDA 路径换成自己的本地副本，输出目录应为新路径。

```sh
python3 stage-applehda.py --gated \
  ./NMXAudio.kext /path/to/AppleHDA.kext ./assembled
codesign --verify --deep --strict ./assembled/NMXAudio.kext
```

组装器会先校验 Apple 签名及 x86_64 架构，然后将完整的 AppleHDA 及其插件放在 `assembled/NMXAudio.kext/Contents/PlugIns/AppleHDA.kext`。输出使用临时签名，组装后的目录才是安装输入。主机使用 AppleALC 的 `as.vit9696.AppleALC` bundle ID，内嵌 AppleHDA 使用原 bundle ID，因此同一开机只应存在各一份。

## 2. 清理现有音频安装

1. 备份实际引导的 OpenCore EFI、备用 EFI 的音频配置、当前音频 kext 和 `/Library/KernelCollections/AuxiliaryKernelExtensions.kc`。保留能够进入恢复环境或旧系统的启动路径。
2. 在 OpenCore 的 `Kernel → Add` 中禁用或移除独立的 `AppleALC.kext`、`AppleALCU.kext`、`SimpleHDA.kext` 及曾手工注入的 `AppleHDA.kext` 和相关插件。先备份相应的 `EFI/OC/Kexts` 文件，清理对应条目并使用同版本 `ocvalidate` 校验。将上一版独立 EFI NMXAudio 也纳入清理。
3. 备份并移走 `/Library/Extensions` 中自行安装的上述音频副本。系统卷中的 Apple 原始文件和其他用途的 Lilu 仍按原配置管理。新版 `NMXAudio.kext` 的安装位置是 **`/Library/Extensions`**。
4. 若此前由 **OCLP 根补丁恢复 AppleHDA**，先核对显卡、无线等其他功能是否也依赖该根补丁快照。OCLP 的 **Post-Install Root Patch → Revert Root Patches** 会恢复整个快照，应在能保留启动和必要硬件支持的情况下执行。恢复后重启，确认原来的 AppleHDA 补丁已经退出，再装 NMXAudio。只删除 OCLP 应用无法撤销根补丁。需要其他根补丁时，请先确认有可用的迁移方案，避免重复引入 AppleHDA。 [OCLP 官方回退文档](https://dortania.github.io/OpenCore-Legacy-Patcher/UNINSTALL.html)

保留原来的 **`alcid=`** 或实际板载控制器上的四字节小端 **`layout-id`**。两者都设置时先用 `alcid=`。例如 ALC897 布局 14 是 `alcid=14`，或 OpenCore `DeviceProperties` 的 Data `0E000000`。移除这两种配置即可选择原生板载后端。可继续使用原有的 `alc-verbs` 设备属性和 `alc-verb` 工具。

## 3. 在隔离目录链接 AuxKC

这一步先生成测试集合，便于在替换正在使用的集合前验证。以下使用 Bash。应在已完成旧音频迁移、能读取 BootKC 与 SystemKC 的目标系统执行。先将当前 `/Library/Extensions` 下仍需使用的 kext 复制进隔离仓库，保持其内部相对路径，再加入上一步生成的 `NMXAudio.kext`。

```bash
job="$(mktemp -d)"
mkdir "$job/Extensions"
sudo ditto /Library/Extensions "$job/Extensions"
if [ -e "$job/Extensions/NMXAudio.kext" ]; then
  sudo mv "$job/Extensions/NMXAudio.kext" "$job/NMXAudio.previous.kext"
fi
sudo ditto --norsrc --noextattr --noacl ./assembled/NMXAudio.kext "$job/Extensions/NMXAudio.kext"
sudo chown -R root:wheel "$job/Extensions"
sudo chmod -R go-w,a+rX "$job/Extensions"

repo="$job/Extensions"
nmx="$repo/NMXAudio.kext"
hda="$nmx/Contents/PlugIns/AppleHDA.kext"
inputs=(--bundle-path "$nmx" --bundle-path "$hda")
for child in AppleHDAController AppleHDAHardwareConfigDriver AppleMikeyDriver DspFuncLib IOHDAFamily; do
  inputs+=(--bundle-path "$hda/Contents/PlugIns/$child.kext")
done

system_kc=/System/Library/KernelCollections
sudo kmutil create --arch x86_64 --new aux --volume-root / \
  --boot-path "$system_kc/BootKernelExtensions.kc" \
  --system-path "$system_kc/SystemKernelExtensions.kc" \
  --repository "$repo" -A "$job/Probe.kc" \
  "${inputs[@]}"
sudo kmutil inspect --arch x86_64 -A "$job/Probe.kc" --show-kext-uuids
```

命令会先把旧 NMXAudio 移出隔离仓库，再加入新版本。用 `kmutil inspect --arch x86_64 -A /Library/KernelCollections/AuxiliaryKernelExtensions.kc --show-kext-uuids` 对比当前集合。若原集合还有其他位于仓库外的 bundle 或需要显式指定的深层插件，将各自的路径加入 `inputs`。安装前应看到原有的非音频驱动，以及 NMXAudio 外层和 6 个嵌入模块。当前 macOS 构建如需 KDK，请使用匹配的 KDK。出现重复 bundle ID、未解析符号或原有驱动遗漏时，先修正输入。

## 4. 安装到系统

1. 确认已有上一步的隔离链接结果和可读的回退备份。将组装后的 **`NMXAudio.kext`** 整体复制至 `/Library/Extensions/NMXAudio.kext`，设置 `root:wheel` 所有权且组及其他用户不可写，并用 `codesign --verify --deep --strict` 复验。若目标已有版本，先将其移到备份，避免合并两棵 bundle。
2. 用第 3 节的同一组输入重建正式集合。将仓库改为 `/Library/Extensions`，七个音频 bundle 参数都改为其最终安装路径。先输出到新文件，再用 `kmutil inspect` 验证实际路径和 UUID 后替换 `/Library/KernelCollections/AuxiliaryKernelExtensions.kc`。隔离集合仍引用隔离用的路径，应由正式链接结果替换。使用系统支持的扩展授权方式完成确认。
3. **可选 HAL**。若需要 AppleHDA 的旧版用户态 HAL 功能，可从本地 AppleHDA 原包复制 Apple 签名的 `AppleHDAHALPlugIn.bundle` 至 `/Library/Audio/Plug-Ins/HAL/`。目标若已有另一版本，先记录并备份。备份并移出这次新增的副本后重启，即可走标准 IOAudio 路径。部分系统进程可能因沙箱限制无法加载这个副本。
4. 保存工作并重启。在「声音」检查板载输入与输出、端口名称、播放和录音。连接音频型显示器时检查实际设备名和音量、左右平衡；拔线后检查输出是否消失。画面的热插拔由显卡驱动处理。

## 5. 核对与回退

```sh
kmutil showloaded --list-only | grep -E 'as\.vit9696\.AppleALC|com\.apple\.driver\.AppleHDA'
ioreg -r -c AppleHDAEngineOutput -d 1
ioreg -r -c NMXAudioEngine -d 1
system_profiler SPAudioDataType
```

使用 `alcid` 或 `layout-id` 时，应能看到 AppleHDA 嵌入模块及对应的板载引擎。未选布局时，板载设备由原生 NMXAudio 引擎注册。两种模式下 NVIDIA 显示器输出都看 NMXAudio 引擎。系统设置和命令行工具可能使用不同的语言选择，端口本地化以系统设置为准。

回退时恢复对应的一整套备份，包括原有 EFI 音频条目、原音频 kext 和对应的旧 AuxKC。若这次新增了 HAL 副本，把这一份移回备份。若迁移前使用了 OCLP 根补丁，按原配置重新应用必要的补丁后再启动。恢复后重启并验证。
