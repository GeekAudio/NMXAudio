// Async startup gate for the embedded stock AppleHDA stack.
// The staged personalities require NMXAudioEmbeddedControllerReady on the
// exact PCI controller and NMXAudioEmbeddedCodecReady on an ancestor.

#include <IOKit/pci/IOPCIDevice.h>
#include <IOKit/IORegistryEntry.h>
#include <IOKit/IOService.h>
#include <IOKit/IOLib.h>
#include <IOKit/IOLocks.h>
#include <libkern/c++/OSNumber.h>
#include <mach/thread_act.h>
#include <kern/thread.h>

#include <stdint.h>
#include <string.h>

extern "C" uint32_t nmx_audio_configured_layout(void *provider);
extern "C" bool nmx_prepare_applehda_controller(IOPCIDevice *provider, uint32_t layout);
extern "C" bool nmx_prepare_applehda_codec(IOPCIDevice *provider);

namespace {

constexpr const char *kControllerReady = "NMXAudioEmbeddedControllerReady";
constexpr const char *kCodecReady = "NMXAudioEmbeddedCodecReady";
constexpr const char *kNativeFallback = "NMXAudioEmbeddedNativeFallback";
constexpr const char *kLayoutID = "layout-id";
constexpr uint16_t kNvidiaVendor = 0x10DE;
constexpr unsigned kCodecWaitMS = 20000;
constexpr unsigned kPollMS = 100;

enum class HostPhase : uint8_t {
	Idle,
	PreparingController,
	ControllerCommitted,
	CodecCommitted,
	NativeFallback,
	FailedAfterController
};

IOLock *gLock {nullptr};
IOPCIDevice *gProvider {nullptr}; // Retained for the combined kext lifetime.
uint32_t gLayout {0};
HostPhase gPhase {HostPhase::Idle};

bool hasAnalogIdentity(IOPCIDevice *provider) {
	if (!provider || provider->configRead16(kIOPCIConfigVendorID) == kNvidiaVendor)
		return false;

	bool namedAnalog = false;
	for (IORegistryEntry *entry = provider; entry; entry = entry->getParentEntry(gIOServicePlane)) {
		const char *name = entry->getName();
		if (name && strcmp(name, "HDAU") == 0)
			return false;
		if (name && (strcmp(name, "HDEF") == 0 || strcmp(name, "HDAS") == 0))
			namedAnalog = true;
	}

	// The layout helper scopes global alcid to HDEF/HDAS. Other controllers
	// must have an explicit layout-id on this exact PCI device.
	return namedAnalog || provider->propertyExists(kLayoutID);
}

bool hasCodecNode(IOPCIDevice *provider) {
	auto iterator = IORegistryIterator::iterateOver(provider, gIOServicePlane,
	                                               kIORegistryIterateRecursively);
	if (!iterator)
		return false;

	IORegistryEntry *entry = nullptr;
	while ((entry = OSDynamicCast(IORegistryEntry, iterator->getNextObject())) != nullptr) {
		auto vendor = OSDynamicCast(OSNumber, entry->getProperty("IOHDACodecVendorID"));
		auto revision = OSDynamicCast(OSNumber, entry->getProperty("IOHDACodecRevisionID"));
		if (vendor && revision && vendor->unsigned32BitValue() != 0)
			return true;
	}
	return false;
}

bool isCodecProvider(IORegistryEntry *entry) {
	return entry->metaCast("IOHDACodecDevice") ||
	       entry->metaCast("AppleHDACodec") ||
	       entry->metaCast("AppleHDACodecGeneric") ||
	       entry->metaCast("IOHDACodecFunction");
}

bool rematchCodecProviders(IOPCIDevice *provider) {
	auto iterator = IORegistryIterator::iterateOver(provider, gIOServicePlane,
	                                               kIORegistryIterateRecursively);
	if (!iterator)
		return false;

	unsigned rematched = 0;
	IORegistryEntry *entry = nullptr;
	while ((entry = OSDynamicCast(IORegistryEntry, iterator->getNextObject())) != nullptr) {
		auto service = OSDynamicCast(IOService, entry);
		if (service && isCodecProvider(entry)) {
			service->registerService();
			++rematched;
		}
	}
	return rematched != 0;
}

void setPhase(HostPhase phase) {
	IOLockLock(gLock);
	gPhase = phase;
	IOLockUnlock(gLock);
}

void fallbackBeforeControllerClaim(IOPCIDevice *provider) {
	// Mark fallback before re-registering so a reentrant probe yields to native
	// audio even if property allocation fails.
	IOLockLock(gLock);
	gPhase = HostPhase::NativeFallback;
	IOPCIDevice *held = gProvider;
	gProvider = nullptr;
	gLayout = 0;
	IOLockUnlock(gLock);

	// Removing the gate first guarantees a setProperty failure cannot leave an
	// enabled AppleHDA controller personality behind.
	provider->removeProperty(kControllerReady);
	provider->removeProperty(kCodecReady);
	provider->setProperty(kControllerReady, false);
	provider->setProperty(kCodecReady, false);
	provider->setProperty(kNativeFallback, true);
	provider->registerService();
	IOLog("NMXAudio: AppleHDA preparation failed before controller claim; using native audio\n");
	if (held)
		held->release();
}

void failAfterControllerClaim() {
	// Do not release the PCI controller to NMXAudio after AppleHDA has been
	// enabled: there is no safe forced teardown/reclaim path.
	setPhase(HostPhase::FailedAfterController);
	IOLog("NMXAudio: AppleHDA codec preparation failed; keeping native takeover disabled\n");
}

void workerEntry(void *argument, wait_result_t) {
	auto provider = static_cast<IOPCIDevice *>(argument);
	IOLockLock(gLock);
	const uint32_t layout = gLayout;
	IOLockUnlock(gLock);

	// The AuxKC is present by now. Do not retry after partial patching.
	if (!nmx_prepare_applehda_controller(provider, layout) ||
		!provider->setProperty(kControllerReady, true)) {
		fallbackBeforeControllerClaim(provider);
		return;
	}
	setPhase(HostPhase::ControllerCommitted);
	provider->registerService();
	IOLog("NMXAudio: AppleHDA controller patches ready for layout %u\n", layout);

	for (unsigned elapsed = 0; elapsed < kCodecWaitMS; elapsed += kPollMS) {
		if (!hasCodecNode(provider)) {
			IOSleep(kPollMS);
			continue;
		}
		// IOParentMatch searches the codec provider's ancestors. The gate is
		// published only on the PCI controller whose patches were installed.
		if (nmx_prepare_applehda_codec(provider) &&
			provider->setProperty(kCodecReady, true) && rematchCodecProviders(provider)) {
			setPhase(HostPhase::CodecCommitted);
			IOLog("NMXAudio: AppleHDA codec patches ready; rematched codec providers\n");
			return;
		}
		break;
	}
	provider->removeProperty(kCodecReady);
	failAfterControllerClaim();
}

} // namespace

extern "C" kern_return_t nmx_audio_embedded_init() {
	gLock = IOLockAlloc();
	return gLock ? KERN_SUCCESS : KERN_RESOURCE_SHORTAGE;
}

extern "C" bool nmx_audio_embedded_defer(IOPCIDevice *provider) {
	if (!provider || !gLock)
		return false;

	IOLockLock(gLock);
	if (gProvider) {
		const bool sameProvider = gProvider == provider;
		IOLockUnlock(gLock);
		return sameProvider;
	}
	if (gPhase != HostPhase::Idle || !hasAnalogIdentity(provider)) {
		IOLockUnlock(gLock);
		return false;
	}
	const uint32_t layout = nmx_audio_configured_layout(provider);
	if (!layout) {
		IOLockUnlock(gLock);
		return false;
	}

	provider->removeProperty(kControllerReady);
	provider->removeProperty(kCodecReady);
	provider->removeProperty(kNativeFallback);
	if (!provider->setProperty(kControllerReady, false) ||
	    !provider->setProperty(kCodecReady, false)) {
		provider->removeProperty(kControllerReady);
		provider->removeProperty(kCodecReady);
		IOLockUnlock(gLock);
		return false;
	}

	provider->retain();
	gProvider = provider;
	gLayout = layout;
	gPhase = HostPhase::PreparingController;
	IOLockUnlock(gLock);

	thread_t worker = THREAD_NULL;
	if (kernel_thread_start(workerEntry, provider, &worker) != KERN_SUCCESS) {
		fallbackBeforeControllerClaim(provider);
		return false;
	}
	thread_deallocate(worker);
	return true;
}
