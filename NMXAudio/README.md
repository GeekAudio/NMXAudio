# NMXAudio builds

For the Tahoe release kit, see [deployment and rollback](DEPLOYMENT.md) and the
[AppleALC PR ledger](APPLEALC-PRS.md).

## System kext (Tahoe)

This is the native-only development build. The Tahoe release uses the
[standalone host with embedded AppleHDA](#tahoe-build-with-embedded-applehda).

The Tahoe system build uses macOS IOAudioFamily and the FreeBSD HDA backend
for the onboard codec and NVIDIA display audio. The single `NMXAudio.kext`
contains its codec configuration and runs independently of AppleHDA,
IOHDAFamily, SimpleHDA and Lilu.

Both builds share the pinned FreeBSD HDA port and the display-audio changes
from [Liberator PR #17](https://github.com/nullmoth/liberator/commit/2e674d1b384729f5879c5b170e5990443090a059).
The display path uses ELD for the monitor name and connection state, and
exposes stereo volume, mute and balance through IOAudioFamily. Set
`nmxaudiolang` to override the system's boot language for onboard jack labels.

Each build packages the complete AppleALC resource tree in
`Contents/Resources/AppleALC/Resources.zip`, with the original relative paths
and bytes. The build and pull request checks verify the archive inventory,
checksums and layout and platform references. Keep codec customizations in the
original AppleALC resource format. [Contributing](../CONTRIBUTING.md)

The Tahoe build compiles `Info.plist`, layout and platform XML, and
`PinConfigs` from the same AppleALC resource tree into native tables. A layout
that uses supported data enters the next build through those files alone.
`alcid` takes precedence over the four-byte `layout-id` property. NMXAudio
matches the selected layout to the detected codec and function group, applies
supported pin and EAPD verbs, and checks the requested `PathMap` connections
against the live codec. FreeBSD supplies the mixer and the route for an
association whose requested path cannot be applied. Without a selected
profile the driver uses the standard FreeBSD settings.

`NATIVE-PROFILES.json` reports the build's routes, pin configuration and
unsupported resource fields. The native backend currently uses its own
controls for volume and mute. AppleHDA binary patches, XML-specific amplifier
and GPIO policies, DSP and vendor verbs outside the supported initialization
set require native equivalents when the hardware needs them. On Tahoe,
`NMXAudioNativeLayout` and `NMXAudioNativePathMapAssociations` show the selected
profile and the number of associations that used `PathMaps` after boot.

The original `alc-verb` utility can issue commands to a native HDA controller
that has a nonzero four-byte `alc-verbs` device property. The `alcverbs` boot
argument takes precedence when set. The native interface supports controllers
with a single attached audio codec.

```sh
NMXAudio/Driver/build.sh \
  /path/to/liberator /path/to/freebsd-src /path/to/nvidia-macos-driver \
  "$HOME/Downloads/NMXAudio-system-build"
```

Install the signed system build in `/Library/Extensions` using the system
auxiliary kernel collection. Confirm the selected layout, playback and wake
behavior on the target machine after reboot.

## AppleALC compatibility build

The AppleALC source, codec layouts, pin configurations, and user-client headers
are kept in this repository. `Driver/` contains the pinned NMXAudio source
patches and FreeBSD-to-XNU compatibility build. The build compiles both sources
and links them once using the AppleALC Xcode target. [AppleALC-IMPORT.json](AppleALC-IMPORT.json)
records the imported PR commits, resolutions and resource validation. It gives the linked
extension the `NMXAudio.kext` filename and preserves AppleALC's bundle ID
and IOKit ABI. The pin tables are installed in the same bundle's Info.plist.

Use Xcode with macOS Kernel.framework headers, Git and Python 3. You also
need a Lilu developer bundle with the headers and plugin source, MacKernelSDK,
and local repositories containing the pinned Liberator, FreeBSD and historical
NVIDIA driver revisions listed in [the NMXAudio driver recipe](Driver/build.sh).
The development build uses Lilu 1.7.3 and MacKernelSDK at
`3f750085caa17ec3a7880f11c11bf4f48cd6a164`.

```sh
NMXAudio/Build/build.sh \
  /path/to/Lilu.kext /path/to/MacKernelSDK /path/to/liberator \
  /path/to/freebsd-src /path/to/nvidia-macos-driver "$HOME/Downloads/NMXAudio-build"
```

The build generates the resource database in a temporary source copy and
writes the signed development kext, build logs and checksums to the output
directory. Use a local output directory because File Provider storage can
add Finder metadata to a signed kext. The verifier checks architecture, one kernel executable, AppleALC
and NMXAudio symbols and personalities, and the complete pin table.

For a hardware test, use the integrated bundle in place of both standalone
audio kexts. Keep Lilu and the system's current AppleHDA compatibility path in
the test configuration. On macOS 26 the analog AppleALC path needs AppleHDA
classes supplied by the system's existing audio stack. The compatibility build
needs Lilu in its linkable kernel collection and has no embedded AppleHDA loader.

### Tahoe build with embedded AppleHDA

The standalone build uses a private kernel patcher compiled from Lilu's source.
It loads no Lilu plug-in and keeps all audio bundles under one
`NMXAudio.kext`. Supply a locally obtained Apple-signed x86_64 AppleHDA stack
and a matching Lilu source checkout. The build validates the supplied stack
before adding per-controller matching conditions and re-signing the modified
bundles. The outer `NMXAudio.kext/Contents/PlugIns` holds `AppleHDA.kext`.
All AppleHDA plug-ins stay in that bundle's own `Contents/PlugIns`.
The gated build declares `OSBundleRequired=Auxiliary` on the six AppleHDA
kernel modules so macOS can include them in the auxiliary collection.

```sh
NMXAudio/Build/build.sh --aux-host /path/to/Lilu-source /path/to/AppleHDA.kext \
  /path/to/Lilu.kext /path/to/MacKernelSDK /path/to/liberator \
  /path/to/freebsd-src /path/to/nvidia-macos-driver "$HOME/Downloads/NMXAudio-embedded"
```

On Tahoe, a positive `alcid=` or an onboard controller's `layout-id` selects
AppleALC. The host asks XNU to load the nested AppleHDA and its dependencies.
After verifying their relocated addresses, it installs the controller patches
and enables the controller match.
When the codec appears, AppleALC selects its compiled layout and platform
resources before enabling the codec driver. The package links the nested
AppleHDA's request and cancel imports to NMXAudio so it can deliver those
resources asynchronously to AppleHDA's callbacks. With no configured layout, the native
driver binds the controller. NVIDIA HDMI and DisplayPort audio use the
native driver. If patching fails before AppleHDA claims the controller, native
matching resumes. Failures after controller matching leave that controller
with AppleHDA for the rest of the boot.

To reproduce the signed host template in the release kit, use
`--aux-host-base <Lilu-source>` in place of `--aux-host <Lilu-source> <AppleHDA.kext>`.
This produces the host template and its manifest. Assemble the template with
`stage-applehda.py --gated` as shown in the [deployment guide](DEPLOYMENT.md).
The validated host build used Lilu source `0249c3a918c68a43891350f35fded615b1575776`.
The deployment guide records the AppleHDA payload version tested on hardware.

The AppleHDA CoreAudio plug-in is optional. To enable it, install the original
Apple-signed `AppleHDAHALPlugIn.bundle` from the embedded AppleHDA's plug-in
directory in `/Library/Audio/Plug-Ins/HAL/`, then restart. The host detects
the copy at startup and publishes its path only for the embedded AppleHDA
engines. Removing that installed copy and restarting selects the standard
IOAudio path. The embedded copy stays with the rest of the AppleHDA stack.

Validate the package against the target Mac's BootKC and SystemKC before
installing. For the isolated link and each AuxKC rebuild, supply a separate
`kmutil create --bundle-path` argument for the outer NMXAudio bundle, AppleHDA,
and each of its five nested kexts. A scan of the outer package directory
alone misses the second level of plug-ins. Verify the linked bundle paths
and symbols, then confirm controller ownership, codec routing, and playback
after reboot.

To check just the unmodified AppleHDA payload's linkability with an existing
build, prepare a temporary collection repository with:

```sh
python3 NMXAudio/Build/stage-applehda.py \
  /path/to/NMXAudio.kext /path/to/AppleHDA.kext /path/to/new-link-check-directory
```

The legacy AppleALC compatibility build still uses standard Lilu plug-in
registration. An EFI-injected Lilu does not appear in the on-disk BootKC used
by `kmutil`.

AppleALC's BSD 3-Clause license is in [LICENSE.txt](../LICENSE.txt).
The NMXAudio source is covered by the [Liberator license](licenses/Liberator-LICENSE.txt)
and the [FreeBSD HD Audio license](licenses/FreeBSD-snd_hda-LICENSE.txt).
The private patcher uses Lilu under its [BSD 3-Clause license](licenses/Lilu-LICENSE.txt).
