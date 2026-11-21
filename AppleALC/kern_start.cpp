//
//  kern_start.cpp
//  AppleALC
//
//  Copyright © 2016-2017 vit9696. All rights reserved.
//

#include <Headers/plugin_start.hpp>
#include <Headers/kern_api.hpp>

#include "kern_alc.hpp"

#if !defined(NMXAUDIO_AUX_HOST)
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
#endif

#if defined(NMXAUDIO_COMBINED) || defined(NMXAUDIO_AUX_HOST)
extern "C" kern_return_t nmx_audio_kmod_start(kmod_info_t *, void *);
extern "C" kern_return_t nmx_audio_kmod_stop(kmod_info_t *, void *);
#if defined(NMXAUDIO_AUX_HOST)
extern "C" void nmx_applealc_module_address(kmod_info_t *);
extern "C" kern_return_t nmx_audio_embedded_init();
#else
extern "C" kern_return_t ADDPR(kern_start)(kmod_info_t *, void *);
extern "C" kern_return_t ADDPR(kern_stop)(kmod_info_t *, void *);
#endif

EXPORT extern "C" kern_return_t NMXAudio_combined_start(kmod_info_t *info, void *data) {
	auto result = nmx_audio_kmod_start(info, data);
	if (result != KERN_SUCCESS)
		return result;

#if defined(NMXAUDIO_AUX_HOST)
	nmx_applealc_module_address(info);
	lilu_get_boot_args("liludelay", &ADDPR(debugPrintDelay), sizeof(ADDPR(debugPrintDelay)));
	ADDPR(debugEnabled) = checkKernelArgument("-alcdbg") || checkKernelArgument("-liludbgall");
	BaseDeviceInfo::init();
	AlcEnabler::createShared();
	if (nmx_audio_embedded_init() != KERN_SUCCESS)
		SYSLOG("aux", "unable to initialize embedded AppleHDA, preserving native audio");
#else
	result = ADDPR(kern_start)(info, data);
	if (result != KERN_SUCCESS)
		nmx_audio_kmod_stop(info, data);
#endif
	return result;
}

EXPORT extern "C" kern_return_t NMXAudio_combined_stop(kmod_info_t *info, void *data) {
#if defined(NMXAUDIO_AUX_HOST)
	// The AppleHDA images can contain routes back into this module.
	return KERN_FAILURE;
#else
	auto result = ADDPR(kern_stop)(info, data);
	if (result != KERN_SUCCESS)
		return result;

	return nmx_audio_kmod_stop(info, data);
#endif
}
#endif
