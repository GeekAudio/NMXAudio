// Satisfy the nested AppleHDA's two layout requests through its own imports.
#include "kern_aux_resources.hpp"

#include <IOKit/IOLocks.h>
#include <IOKit/IOService.h>
#include <kern/thread_call.h>
#include <string.h>
#include <sys/vnode.h>

namespace {

constexpr char appleHDAIdentifier[] = "com.apple.driver.AppleHDA";
constexpr char halBundle[] = "/Library/Audio/Plug-Ins/HAL/AppleHDAHALPlugIn.bundle";
constexpr char halRelativeBundle[] = "../../../Library/Audio/Plug-Ins/HAL/AppleHDAHALPlugIn.bundle";
// Keep local tags away from XNU's counter, which starts at zero per boot.
constexpr OSKextRequestTag firstLocalTag = kOSKextRequestTagInvalid - 65535;

IOLock *requestLock;
thread_call_t requestWorker;
NMXAux::Resource layoutResource;
NMXAux::Resource platformResource;
OSKextRequestTag nextTag = firstLocalTag;
bool halAvailable;

bool hasHALFile(vfs_context_t context, const char *relativePath) {
	char path[160];
	snprintf(path, sizeof(path), "%s/%s", halBundle, relativePath);
	vnode_t file = nullptr;
	if (vnode_lookup(path, 0, &file, context))
		return false;
	bool regular = vnode_vtype(file) == VREG;
	vnode_put(file);
	return regular;
}

struct Request {
	OSKextRequestTag tag;
	const NMXAux::Resource *resource;
	IOService *owner;
	void *context;
};

Request pending[16];

void deliverResources(thread_call_param_t, thread_call_param_t) {
	for (;;) {
		Request request {};
		IOLockLock(requestLock);
		for (auto &entry : pending) {
			if (entry.resource) {
				request = entry;
				entry = {};
				break;
			}
		}
		IOLockUnlock(requestLock);
		if (!request.resource)
			return;

		const auto &resource = *request.resource;
		// Once removed, the request is in delivery and cannot be canceled.
		// The device and kext remain retained while the callback runs.
		resource.callback(request.tag, kOSReturnSuccess, resource.data, resource.size, request.context);
		request.owner->release();
	}
}

} // namespace

bool NMXAux::initializeResources(OSKextLoadTag appleHDALoadTag) {
	auto lock = IOLockAlloc();
	if (!lock)
		return false;
	auto worker = thread_call_allocate(deliverResources, nullptr);
	// The AppleHDA patches and callbacks are installed for this boot.
	if (!worker || OSKextRetainKextWithLoadTag(appleHDALoadTag) != kOSReturnSuccess) {
		if (worker)
			thread_call_free(worker);
		IOLockFree(lock);
		return false;
	}
	requestWorker = worker;
	requestLock = lock;
	auto context = vfs_context_create(nullptr);
	if (context) {
		halAvailable = hasHALFile(context, "Contents/Info.plist") &&
			hasHALFile(context, "Contents/MacOS/AppleHDAHALPlugIn");
		vfs_context_rele(context);
	}
	return true;
}

bool NMXAux::setResources(const Resource &layout, const Resource &platform) {
	if (!requestLock || !layout.callback || !layout.data || !layout.size ||
		!platform.callback || !platform.data || !platform.size || layout.callback == platform.callback)
		return false;
	IOLockLock(requestLock);
	// Resource descriptors live in the combined kext for the entire boot.
	bool unset = !layoutResource.callback && !platformResource.callback;
	if (unset) {
		layoutResource = layout;
		platformResource = platform;
	}
	IOLockUnlock(requestLock);
	return unset;
}

extern "C" __attribute__((visibility("default"))) OSReturn NMXHDARequestResource(
	const char *identifier, const char *name, OSKextRequestResourceCallback callback,
	void *context, OSKextRequestTag *tag) {
	if (!requestLock || !identifier || strcmp(identifier, appleHDAIdentifier) || !name || !callback)
		return OSKextRequestResource(identifier, name, callback, context, tag);

	IOLockLock(requestLock);
	const NMXAux::Resource *resource = nullptr;
	if (callback == layoutResource.callback)
		resource = &layoutResource;
	else if (callback == platformResource.callback)
		resource = &platformResource;
	if (!resource) {
		IOLockUnlock(requestLock);
		return OSKextRequestResource(identifier, name, callback, context, tag);
	}

	if (tag)
		*tag = kOSKextRequestTagInvalid;
	// Both profiled AppleHDADriver callbacks pass the audio device as context.
	auto owner = OSDynamicCast(IOService, static_cast<OSObject *>(context));
	if (!owner) {
		IOLockUnlock(requestLock);
		return kOSKextReturnInvalidArgument;
	}
	for (auto &entry : pending) {
		if (nextTag == kOSKextRequestTagInvalid)
			break;
		if (entry.resource)
			continue;
		owner->retain();
		entry = {nextTag++, resource, owner, context};
		if (tag)
			*tag = entry.tag;
		thread_call_enter(requestWorker);
		IOLockUnlock(requestLock);
		return kOSReturnSuccess;
	}
	IOLockUnlock(requestLock);
	return kOSKextReturnNoResources;
}

extern "C" __attribute__((visibility("default"))) OSReturn NMXHDACancelRequest(
	OSKextRequestTag tag, void **context) {
	if (!requestLock || tag < firstLocalTag)
		return OSKextCancelRequest(tag, context);

	IOLockLock(requestLock);
	for (auto &entry : pending) {
		if (!entry.resource || entry.tag != tag)
			continue;
		auto request = entry;
		entry = {};
		IOLockUnlock(requestLock);
		if (context)
			*context = request.context;
		request.owner->release();
		return kOSReturnSuccess;
	}
	IOLockUnlock(requestLock);
	return kOSKextReturnNotFound;
}

// Only the nested AppleHDA engines route their C-string setter here.
extern "C" __attribute__((visibility("default"))) bool NMXHDASetEmbeddedRegistryCStringProperty(
	IORegistryEntry *entry, const char *key, const char *value) {
	if (key && value && strcmp(key, "IOAudioEngineCoreAudioPlugIn") == 0 &&
		strcmp(value, "AppleHDA.kext/Contents/PlugIns/AppleHDAHALPlugIn.bundle") == 0) {
		if (!halAvailable) {
			entry->IORegistryEntry::removeProperty(key);
			return true;
		}
		// CoreAudio resolves this path relative to /System/Library/Extensions.
		value = halRelativeBundle;
	}
	return entry->IORegistryEntry::setProperty(key, value);
}
