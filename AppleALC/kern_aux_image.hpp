// Helpers for AppleHDA images in the same x86_64 auxiliary kernel collection.
#ifndef kern_aux_image_hpp
#define kern_aux_image_hpp

#include <Headers/kern_patcher.hpp>
#include <mach-o/loader.h>
#include <mach/vm_prot.h>

namespace NMXAux {

constexpr int auxiliaryCollection = 3; // XNU kc_kind_t::KCKindAuxiliary
constexpr uint32_t maxHeaderCommands = 0x10000;

struct Image {
	mach_vm_address_t address {0};
	size_t size {0};
};

inline mach_vm_address_t filesetAddress(const mach_header_64 *header, const char *identifier) {
	if (!header || header->magic != MH_MAGIC_64 || header->filetype != MH_FILESET ||
		header->sizeofcmds > maxHeaderCommands)
		return 0;

	auto cursor = reinterpret_cast<const uint8_t *>(header + 1);
	auto remaining = header->sizeofcmds;
	for (uint32_t index = 0; index < header->ncmds; ++index) {
		if (remaining < sizeof(load_command))
			return 0;
		auto command = reinterpret_cast<const load_command *>(cursor);
		if (command->cmdsize < sizeof(load_command) || command->cmdsize > remaining)
			return 0;
		if (command->cmd == LC_FILESET_ENTRY && command->cmdsize >= sizeof(fileset_entry_command)) {
			auto entry = reinterpret_cast<const fileset_entry_command *>(cursor);
			size_t offset = entry->entry_id.offset;
			size_t length = strlen(identifier);
			if (offset >= sizeof(*entry) && offset < command->cmdsize &&
				length < command->cmdsize - offset &&
				memcmp(cursor + offset, identifier, length + 1) == 0)
				return entry->vmaddr;
		}
		cursor += command->cmdsize;
		remaining -= command->cmdsize;
	}
	return 0;
}

inline mach_vm_address_t imageAddress(const mach_header_64 *header,
									  mach_vm_address_t selfAddress, const char *identifier) {
	auto self = filesetAddress(header, "as.vit9696.AppleALC");
	auto target = filesetAddress(header, identifier);
	if (!self || !target || selfAddress < self || target > UINT64_MAX - (selfAddress - self))
		return 0;
	return target + (selfAddress - self);
}

inline bool isRelocatedImage(mach_vm_address_t address) {
	if (!address)
		return false;
	auto image = reinterpret_cast<const mach_header_64 *>(address);
	if (image->magic != MH_MAGIC_64 || image->filetype != MH_KEXT_BUNDLE ||
		image->sizeofcmds > maxHeaderCommands)
		return false;

	// Pageable AuxKC entries are mapped before XNU loads the kext. The
	// header is readable at that point, but its segment and symbol addresses
	// are only relocated by the loader. Lilu expects the relocated image.
	auto cursor = reinterpret_cast<const uint8_t *>(image + 1);
	auto remaining = image->sizeofcmds;
	for (uint32_t index = 0; index < image->ncmds; ++index) {
		if (remaining < sizeof(load_command))
			return false;
		auto command = reinterpret_cast<const load_command *>(cursor);
		if (command->cmdsize < sizeof(load_command) || command->cmdsize > remaining)
			return false;
		if (command->cmd == LC_SEGMENT_64 && command->cmdsize >= sizeof(segment_command_64)) {
			auto segment = reinterpret_cast<const segment_command_64 *>(cursor);
			if (strncmp(segment->segname, "__TEXT", sizeof(segment->segname)) == 0)
				return segment->vmaddr == address;
		}
		cursor += command->cmdsize;
		remaining -= command->cmdsize;
	}
	return false;
}

inline Image bindImage(KernelPatcher &patcher, mach_vm_address_t address, KernelPatcher::KextInfo &info) {
	if (!isRelocatedImage(address)) {
		SYSLOG("aux", "%s has no relocated __TEXT segment", info.id);
		return {};
	}

	auto index = patcher.loadKinfo(&info);
	if (index == KernelPatcher::KernelID || patcher.getError() != KernelPatcher::Error::NoError)
		return {};
	auto size = patcher.updateRunningInfo(index, address);
	if (patcher.getError() != KernelPatcher::Error::NoError || size < sizeof(mach_header_64))
		return {};
	return {address, size};
}

// Called only for an image whose relocated header has already been bound.
inline bool segmentContains(Image image, const char *name, mach_vm_address_t address,
							size_t length, vm_prot_t protection) {
	auto header = reinterpret_cast<const mach_header_64 *>(image.address);
	auto cursor = reinterpret_cast<const uint8_t *>(header + 1);
	auto remaining = header->sizeofcmds;
	for (uint32_t index = 0; index < header->ncmds; ++index) {
		if (remaining < sizeof(load_command))
			return false;
		auto command = reinterpret_cast<const load_command *>(cursor);
		if (command->cmdsize < sizeof(load_command) || command->cmdsize > remaining)
			return false;
		if (command->cmd == LC_SEGMENT_64 && command->cmdsize >= sizeof(segment_command_64)) {
			auto segment = reinterpret_cast<const segment_command_64 *>(cursor);
			if (strncmp(segment->segname, name, sizeof(segment->segname)) == 0 &&
				(segment->initprot & protection) == protection && address >= segment->vmaddr) {
				auto offset = address - segment->vmaddr;
				return offset <= segment->vmsize && length <= segment->vmsize - offset;
			}
		}
		cursor += command->cmdsize;
		remaining -= command->cmdsize;
	}
	return false;
}

} // namespace NMXAux

#endif
