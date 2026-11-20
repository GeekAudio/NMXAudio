//
//  kern_start.cpp
//  AppleALC
//
//  Copyright © 2016-2017 vit9696. All rights reserved.
//

#include <Headers/plugin_start.hpp>
#include <Headers/kern_api.hpp>

#include "kern_alc.hpp"

static const char *bootargOff[] {
	"-alcoff"
};

static const char *bootargDebug[] {
	"-alcdbg"
};

static const char *bootargBeta[] {
	"-alcbeta"
};

PluginConfiguration ADDPR(config) {
	xStringify(PRODUCT_NAME),
	parseModuleVersion(xStringify(MODULE_VERSION)),
	LiluAPI::AllowNormal | LiluAPI::AllowInstallerRecovery,
	bootargOff,
	arrsize(bootargOff),
	bootargDebug,
	arrsize(bootargDebug),
	bootargBeta,
	arrsize(bootargBeta),
	KernelVersion::Tiger,
	KernelVersion::Tahoe,
	[]() {
		AlcEnabler::createShared();
		AlcEnabler::getShared()->init();
	}
};

#if defined(NMXAUDIO_COMBINED)
extern "C" kern_return_t nmx_audio_kmod_start(kmod_info_t *, void *);
extern "C" kern_return_t nmx_audio_kmod_stop(kmod_info_t *, void *);
extern "C" kern_return_t ADDPR(kern_start)(kmod_info_t *, void *);
extern "C" kern_return_t ADDPR(kern_stop)(kmod_info_t *, void *);

EXPORT extern "C" kern_return_t NMXAudio_combined_start(kmod_info_t *info, void *data) {
	auto result = nmx_audio_kmod_start(info, data);
	if (result != KERN_SUCCESS)
		return result;

	result = ADDPR(kern_start)(info, data);
	if (result != KERN_SUCCESS)
		nmx_audio_kmod_stop(info, data);
	return result;
}

EXPORT extern "C" kern_return_t NMXAudio_combined_stop(kmod_info_t *info, void *data) {
	auto result = ADDPR(kern_stop)(info, data);
	if (result != KERN_SUCCESS)
		return result;

	return nmx_audio_kmod_stop(info, data);
}
#endif
