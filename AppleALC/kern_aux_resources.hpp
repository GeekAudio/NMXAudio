#ifndef kern_aux_resources_hpp
#define kern_aux_resources_hpp

#include <libkern/OSKextLib.h>

class IORegistryEntry;

extern "C" bool NMXHDASetEmbeddedRegistryCStringProperty(IORegistryEntry *, const char *, const char *);

namespace NMXAux {

struct Resource {
	OSKextRequestResourceCallback callback;
	const void *data;
	uint32_t size;
};

bool initializeResources(OSKextLoadTag appleHDALoadTag);
bool setResources(const Resource &layout, const Resource &platform);

} // namespace NMXAux

#endif
