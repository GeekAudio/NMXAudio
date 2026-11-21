<div align="center">

# NMXAudio

**在 Intel macOS Tahoe 上使用板载 HDA 和 NVIDIA 显示器音频**

简体中文 · [English](README.en.md)

[下载](https://github.com/GeekAudio/NMXAudio/releases/latest) · [安装与回退](NMXAudio/DEPLOYMENT.md) · [AppleALC PR](NMXAudio/APPLEALC-PRS.md)

</div>

## 要求

| 项目 | 要求 |
| --- | --- |
| 系统 | Intel macOS Tahoe 26 / Darwin 25，需具备安装系统内核扩展的条件 |
| 设备 | PCI HDA 板载控制器，或支持音频的 NVIDIA HDMI / DisplayPort 输出 |
| 组装 | Python 3、Xcode Command Line Tools、本机取得的完整 Apple 签名 `AppleHDA.kext`。实机验证使用 600.2 |
| 位置 | `/Library/Extensions/NMXAudio.kext`，通过 AuxKC 加载 |

## 安装

1. 下载 ZIP 和 `SHA256SUMS`，校验并解压。用自备的 AppleHDA 与包内工具组装完整的 `NMXAudio.kext`。
2. 备份引导配置。停用 EFI 和系统中重复的 AppleALC、AppleHDA 和 SimpleHDA。如果是 OCLP 根补丁提供的 AppleHDA，按安装指南先处理快照和其他补丁的依赖。
3. 预检 AuxKC，再将完整的 kext 装入 `/Library/Extensions`，重建 AuxKC 后重启。组装、备份和核对命令见 [安装与回退](NMXAudio/DEPLOYMENT.md)。

## 布局与 HAL

| 配置 | 板载音频 |
| --- | --- |
| `alcid=` 正值或板载控制器上的四字节 `layout-id` | AppleALC 加载布局，内嵌 AppleHDA 驱动板载声卡 |
| 无布局配置 | 由 NMXAudio 的原生 HDA 后端接管 |

NVIDIA HDMI / DP 音频统一由原生后端处理，可用真实显示器名称、音量、静音和左右平衡。板载端口名称在系统设置中按系统语言显示。AppleHDA HAL 为可选项，安装路径和退出方式见 [HAL 安装说明](NMXAudio/DEPLOYMENT.md#4-安装到系统)。

## 开发

[构建说明](NMXAudio/README.md) · [声卡配置贡献](CONTRIBUTING.md) · [15 个 AppleALC PR 的原始提交与合并记录](NMXAudio/APPLEALC-PRS.md)

[AppleALC 中文文档](README_CN.md) · [AppleALC 上游文档](https://github.com/acidanthera/AppleALC/wiki)

<details>
<summary>AppleALC 上游项目说明</summary>

AppleALC
========

[![Build Status](https://github.com/acidanthera/AppleALC/actions/workflows/main.yml/badge.svg?branch=master)](https://github.com/acidanthera/AppleALC/actions) [![Scan Status](https://scan.coverity.com/projects/16166/badge.svg?flat=1)](https://scan.coverity.com/projects/16166)

An open source kernel extension enabling native macOS HD audio for not officially supported codecs without any filesystem modifications. AppleALCU can be used for systems with digital-only audio.

English (Current)  
[简体中文](https://github.com/acidanthera/AppleALC/blob/master/README_CN.md)  

#### Features
- Digital and analog audio support starting from the OS installation
- Recovery HD/macOS Installer audio support
- Automated codec detection
- Unsupported audio controller enabling (internal and external)
- Arbitrary kext patching
- Custom platform/layout injection
- Works with SIP / El Capitan+
- Currently compatible with 10.4-26*

\* _NOTE_: macOS 26 dropped AppleHDA.kext in DP2. AppleALC functioning on macOS 26 may require additional actions if AppleHDA.kext is necessary.

#### Credits
- [Apple](https://www.apple.com) for macOS  
- [Onyx The Black Cat](https://github.com/gdbinit/onyx-the-black-cat) by [fG!](https://reverse.put.as) for the base of the kernel patcher
- [capstone](https://github.com/aquynh/capstone) by [Nguyen Anh Quynh](https://github.com/aquynh) for the disassembler module
- [toleda](https://github.com/toleda), [Mirone](https://github.com/Mirone) and certain others for audio patches and layouts
- [Pike R. Alpha](https://github.com/Piker-Alpha) for [lzvn](https://github.com/Piker-Alpha/LZVN) decompression and certain HDMI patches
- [07151129](https://github.com/07151129) for some code parts and suggestions
- [roddy20](https://github.com/roddy20) for training and research dumps to a patching of codecs
- [vit9696](https://github.com/vit9696) for writing the software and maintaining it
- [Andrey1970AppleLife](https://github.com/Andrey1970AppleLife), [vandroiy2013](https://github.com/vandroiy2013) for maintaining the codec database

#### Installation
The minimal instruction is available on the [wiki](https://github.com/acidanthera/AppleALC/wiki).  
The prebuilt binaries are available on [releases](https://github.com/acidanthera/AppleALC/releases) page.

#### Contribution
To support more audio codecs in the binary packages you are asked to submit your configurations. Please read the [wiki](https://github.com/acidanthera/AppleALC/wiki) for more details. For the contributors with programming skills the headers are filled with AppleDOC comments.

#### Support and discussion
[InsanelyMac topic](http://www.insanelymac.com/forum/topic/311293-applealc-—-dynamic-applehda-patching/) in English  
[AppleLife topic](https://applelife.ru/threads/applealc-dinamicheskij-patching-applehda.1171672/) in Russian

#### Donations
Writing and supporting code is fun but it takes time. If you want to thank the author for his work consider contributing, bugreporting, or providing the support to other users.

</details>
