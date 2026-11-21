# Contributing codec support

Codec contributions use the existing AppleALC resource format. Start with the
official [Adding codec support guide](https://github.com/acidanthera/AppleALC/wiki/Adding-codec-support)
and follow its resource layout so codec work remains familiar to AppleALC
contributors. For installation and baseline hardware tests, use the complete kext
from [Releases](https://github.com/GeekAudio/NMXAudio/releases/latest) and follow
the [deployment guide](NMXAudio/DEPLOYMENT.md).

## Resource files

Put each codec's source resources under `Resources/<Codec>/`:

- `Info.plist` identifies the codec and registers its layout and, where needed,
  platform files by layout ID.
- `layout*.xml` describes the AppleALC layout resources.
- `Platforms*.xml` supplies platform resources when the layout needs them.

Keep the original XML and plist source files in the PR. Add or update the
corresponding entries in `Resources/<Codec>/Info.plist`; include a short
comment naming the board or laptop and the contributor, as in AppleALC.
Preserve upstream attribution and licensing for imported resources. When a
layout ID has several macOS version variants, keep their version ranges
distinct. Keep each PR focused on its codec changes.

Register required pin configuration in the existing
`Resources/PinConfigs.kext/Contents/Info.plist` table. Include the full codec ID,
layout ID, function group and measured `ConfigData`. Add wake data after testing
sleep and resume. Use `Resources/Vendors.plist` or `Resources/Controllers.plist`
if the change requires a new vendor or controller entry. Both builds use the same
AppleALC resource tree.

## Hardware evidence

Include enough information for another maintainer to reproduce the mapping:

- Motherboard or laptop model, audio codec, codec vendor/revision ID, and audio
  controller path from IORegistry. Include the relevant subsystem ID when it
  identifies the board.
- The selected layout ID and how it is selected (`alcid` or the four-byte
  `layout-id` property).
- macOS version and a concise test table for each claimed route: connector,
  output or input, expected behavior, and observed result. Note microphone,
  jack detection, simultaneous outputs, and sleep/wake only when tested.
- A codec dump or relevant logs when they help explain the pin mapping. Remove
  serial numbers and other personal identifiers before attaching them.

Do not describe a route as working based only on a generated resource bundle or
successful build. State which machine and physical ports were actually tested.

## Native Tahoe backend scope

A layout that uses supported `PathMaps` and initialization data is compiled
automatically into the next Tahoe build. Submit the original AppleALC files.
At startup NMXAudio checks the generated paths against the codec's live widget
graph and hands unsupported associations to FreeBSD discovery. Follow the
[NMXAudio build and support notes](NMXAudio/README.md) to check routing after boot.

The native profile report lists any source features the backend cannot apply.
Include required codec, pin and verb evidence for layouts that rely on those
features. Hardware evidence is required for claims about playback, pin
detection or resume on a specific machine.

## Build and pull request

Run the resource check locally with Python 3. The pull request workflow runs
the same check.

```sh
work="$(mktemp -d)"
python3 NMXAudio/Driver/package-applealc-resources.py Resources "$work/AppleALC"
python3 NMXAudio/Driver/package-applealc-resources.py Resources "$work/AppleALC" --verify
python3 NMXAudio/Driver/generate-native-profiles.py Resources \
    "$work/nmx_native_profiles.h" --darwin-major 25 \
    --report "$work/native-profiles.json"
```

Use the repository's [native Tahoe build instructions](NMXAudio/README.md#system-kext-tahoe)
for pin-table or native-backend changes. For changes to the AppleALC-compatible
source build, see [its build instructions](NMXAudio/README.md#applealc-compatibility-build).
Run the build relevant to your change and include its result, along with the
hardware evidence above, in the PR description. Keep codec PRs focused on
source files. Maintainers update `dist/NMXAudio.kext` when tagging a release.
