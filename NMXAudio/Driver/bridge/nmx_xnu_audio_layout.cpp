/* Read the explicitly configured layout for an onboard HD Audio controller. */
#include <IOKit/pci/IOPCIDevice.h>
#include <libkern/c++/OSData.h>
#include <libkern/c++/OSNumber.h>
#include <libkern/version.h>
#include <pexpert/pexpert.h>

namespace {

constexpr uint16_t kNvidiaVendorId = 0x10de;

IOPCIDevice *audioPCI(void *controller)
{
    auto *pci = OSDynamicCast(IOPCIDevice, static_cast<OSObject *>(controller));
    if (pci == nullptr || (pci->configRead32(kIOPCIConfigRevisionID) >> 16) != 0x0403)
        return nullptr;
    return pci;
}

uint32_t configuredLayout(IOPCIDevice *pci)
{
    if (pci == nullptr || pci->configRead16(kIOPCIConfigVendorID) == kNvidiaVendorId)
        return 0;

    int overrideLayout = 0;
    if (PE_parse_boot_argn("alcid", &overrideLayout, sizeof(overrideLayout)))
        return overrideLayout > 0 ? static_cast<uint32_t>(overrideLayout) : 0;

    auto *property = pci->getProperty("layout-id");
    if (auto *number = OSDynamicCast(OSNumber, property))
        return number->unsigned32BitValue();
    auto *data = OSDynamicCast(OSData, property);
    if (!data || data->getLength() != 4)
        return 0;
    auto *bytes = static_cast<const uint8_t *>(data->getBytesNoCopy());
    if (!bytes)
        return 0;
    return static_cast<uint32_t>(bytes[0]) | (static_cast<uint32_t>(bytes[1]) << 8) |
        (static_cast<uint32_t>(bytes[2]) << 16) | (static_cast<uint32_t>(bytes[3]) << 24);
}

} // namespace

extern "C" uint32_t nmx_audio_configured_layout(void *controller)
{
    return configuredLayout(audioPCI(controller));
}

/* Keep the native-profile caller on the same parser as startup selection. */
extern "C" uint32_t nmx_audio_native_layout(void *controller)
{
    return version_major == NMX_NATIVE_PROFILE_DARWIN ? nmx_audio_configured_layout(controller) : 0;
}

extern "C" void nmx_audio_native_publish(void *controller, uint32_t layout)
{
    auto *pci = audioPCI(controller);
    if (!pci)
        return;
    if (layout == 0)
        pci->removeProperty("NMXAudioNativeLayout");
    else
        pci->setProperty("NMXAudioNativeLayout", layout, 32);
}

extern "C" void nmx_audio_native_publish_routes(void *controller, uint32_t count)
{
    auto *pci = audioPCI(controller);
    if (pci)
        pci->setProperty("NMXAudioNativePathMapAssociations", count, 32);
}
