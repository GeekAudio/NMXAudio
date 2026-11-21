# NMXAudio

Onboard HDA and NVIDIA HDMI / DisplayPort audio for x86_64 macOS Tahoe.

[简体中文](README.md) · [Download](https://github.com/GeekAudio/NMXAudio/releases/latest) · [Deployment](NMXAudio/DEPLOYMENT.md) · [AppleALC PRs](NMXAudio/APPLEALC-PRS.md)

## Requirements

- x86_64 macOS 26 (Darwin 25) with a PCI HDA controller or a supported NVIDIA display-audio output.
- Python 3, Xcode Command Line Tools, and a complete, locally obtained Apple-signed x86_64 AppleHDA stack. Hardware verification used AppleHDA 600.2.

## Installation

1. Verify the ZIP against `SHA256SUMS` and extract it. Run `python3 stage-applehda.py --gated ./NMXAudio.kext /path/to/AppleHDA.kext ./assembled`.
2. Back up the active EFI, existing audio extensions and AuxKC. Remove duplicate AppleALC, AppleHDA and SimpleHDA installations. If AppleHDA was restored by OCLP, review other patch dependencies before reverting its root snapshot.
3. Use the [deployment guide](NMXAudio/DEPLOYMENT.md) to link an isolated AuxKC first. Install the assembled `NMXAudio.kext` in `/Library/Extensions`, rebuild the live AuxKC and restart.

A positive `alcid=` or the onboard controller's `layout-id` selects the integrated AppleALC and AppleHDA path. With neither configured, the native HDA backend handles onboard audio. NVIDIA audio uses the native backend in both modes. The optional AppleHDA HAL plug-in goes in `/Library/Audio/Plug-Ins/HAL/`.

## Development

[Build instructions](NMXAudio/README.md) · [Codec contributions](CONTRIBUTING.md) · [Imported AppleALC commits](NMXAudio/APPLEALC-PRS.md)
