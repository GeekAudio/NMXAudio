/* Native IOUserClient bridge for the AppleALC alc-verb command ABI. */
#include <stdint.h>

#include <IOKit/IOService.h>
#include <IOKit/IOUserClient.h>
#include <IOKit/IOLib.h>
#include <IOKit/pci/IOPCIDevice.h>
#include <libkern/c++/OSBoolean.h>
#include <libkern/c++/OSData.h>
#include <pexpert/pexpert.h>

namespace {

constexpr uint32_t kExecuteVerbSelector = 0;
constexpr uint32_t kExecuteVerbInputCount = 3;
constexpr uint32_t kExecuteVerbOutputCount = 1;
constexpr uint32_t kMaxCodecNode = 0xff;
constexpr uint32_t kMaxCodecVerb = 0xfff;
constexpr uint32_t kMaxCodecVerbParameter = 0xffff;

bool verbsAllowed(IOPCIDevice *pci)
{
    uint32_t bootArgument = 0;
    if (PE_parse_boot_argn("alcverbs", &bootArgument, sizeof(bootArgument)))
        return bootArgument != 0;

    OSObject *property = pci->copyProperty("alc-verbs");
    if (property == nullptr)
        return false;
    auto *value = OSDynamicCast(OSData, property);
    uint32_t enabled = 0;
    if (value != nullptr && value->getLength() == sizeof(enabled))
        memcpy(&enabled, value->getBytesNoCopy(), sizeof(enabled));
    property->release();
    return enabled != 0;
}

IOPCIDevice *controllerPCI(IOService *controller)
{
    if (controller == nullptr)
        return nullptr;
    return OSDynamicCast(IOPCIDevice, controller->getProvider());
}

} // namespace

/* Implemented by the native NMXAudio service. It serializes access against
 * controller teardown and routes the command to the FreeBSD HDA instance
 * attached to this exact PCI controller. */
extern "C" IOReturn nmx_audio_native_execute_verb(
    void *nmxAudioService,
    uint16_t nid,
    uint16_t verb,
    uint16_t parameter,
    uint32_t *response);

class ALCUserClientProvider : public IOService {
    OSDeclareDefaultStructors(ALCUserClientProvider);

    IOService *controller_ { nullptr };
    IOPCIDevice *pci_ { nullptr };
    bool readyForVerbs_ { false };

public:
    IOService *probe(IOService *provider, SInt32 *score) override;
    bool start(IOService *provider) override;
    void stop(IOService *provider) override;
    void free() override;

    IOReturn executeVerb(uint32_t nid, uint32_t verb, uint32_t parameter,
                         uint32_t *response);
};

class ALCUserClient : public IOUserClient {
    OSDeclareDefaultStructors(ALCUserClient);

    ALCUserClientProvider *provider_ { nullptr };

    static IOReturn methodExecuteVerb(OSObject *target, void *reference,
                                      IOExternalMethodArguments *arguments);

public:
    bool initWithTask(task_t owningTask, void *securityToken,
                      UInt32 type, OSDictionary *properties) override;
    bool start(IOService *provider) override;
    void free() override;
    IOReturn clientClose() override;
    IOReturn externalMethod(uint32_t selector,
                            IOExternalMethodArguments *arguments,
                            IOExternalMethodDispatch *dispatch,
                            OSObject *target, void *reference) override;
};

OSDefineMetaClassAndStructors(ALCUserClientProvider, IOService);
OSDefineMetaClassAndStructors(ALCUserClient, IOUserClient);

IOService *ALCUserClientProvider::probe(IOService *provider, SInt32 *score)
{
    auto *pci = controllerPCI(provider);
    if (pci == nullptr || !verbsAllowed(pci))
        return nullptr;

    if (score != nullptr)
        *score = 1000;
    return IOService::probe(provider, score);
}

bool ALCUserClientProvider::start(IOService *provider)
{
    auto *pci = controllerPCI(provider);
    if (pci == nullptr || !verbsAllowed(pci) || !IOService::start(provider))
        return false;

    controller_ = provider;
    controller_->retain();
    pci_ = pci;
    pci_->retain();
    __atomic_store_n(&readyForVerbs_, true, __ATOMIC_RELEASE);
    setProperty("ReadyForALCVerbs", kOSBooleanTrue);
    registerService();
    return true;
}

void ALCUserClientProvider::stop(IOService *provider)
{
    __atomic_store_n(&readyForVerbs_, false, __ATOMIC_RELEASE);
    IOService::stop(provider);
}

void ALCUserClientProvider::free()
{
    if (pci_ != nullptr)
        pci_->release();
    if (controller_ != nullptr)
        controller_->release();
    IOService::free();
}

IOReturn ALCUserClientProvider::executeVerb(uint32_t nid, uint32_t verb,
                                           uint32_t parameter,
                                           uint32_t *response)
{
    if (!__atomic_load_n(&readyForVerbs_, __ATOMIC_ACQUIRE))
        return kIOReturnNotReady;
    if (!verbsAllowed(pci_))
        return kIOReturnNotPermitted;

    return nmx_audio_native_execute_verb(controller_,
        static_cast<uint16_t>(nid), static_cast<uint16_t>(verb),
        static_cast<uint16_t>(parameter), response);
}

bool ALCUserClient::initWithTask(task_t owningTask, void *securityToken,
                                 UInt32 type, OSDictionary *properties)
{
    if (owningTask == nullptr ||
        !IOUserClient::initWithTask(owningTask, securityToken, type, properties))
        return false;
    provider_ = nullptr;
    return true;
}

bool ALCUserClient::start(IOService *provider)
{
    auto *verbProvider = OSDynamicCast(ALCUserClientProvider, provider);
    if (verbProvider == nullptr || !IOUserClient::start(provider))
        return false;
    provider_ = verbProvider;
    provider_->retain();
    return true;
}

void ALCUserClient::free()
{
    if (provider_ != nullptr)
        provider_->release();
    IOUserClient::free();
}

IOReturn ALCUserClient::clientClose()
{
    if (!isInactive())
        terminate();
    return kIOReturnSuccess;
}

IOReturn ALCUserClient::methodExecuteVerb(OSObject *target, void *,
                                          IOExternalMethodArguments *arguments)
{
    auto *provider = OSDynamicCast(ALCUserClientProvider, target);
    if (provider == nullptr || arguments == nullptr ||
        arguments->scalarInput == nullptr || arguments->scalarOutput == nullptr ||
        arguments->scalarInputCount != kExecuteVerbInputCount ||
        arguments->scalarOutputCount != kExecuteVerbOutputCount)
        return kIOReturnBadArgument;

    const uint64_t nid = arguments->scalarInput[0];
    const uint64_t verb = arguments->scalarInput[1];
    const uint64_t parameter = arguments->scalarInput[2];
    if (nid > kMaxCodecNode || verb > kMaxCodecVerb ||
        parameter > kMaxCodecVerbParameter)
        return kIOReturnBadArgument;

    uint32_t response = 0;
    IOReturn result = provider->executeVerb(
        static_cast<uint32_t>(nid), static_cast<uint32_t>(verb),
        static_cast<uint32_t>(parameter), &response);
    if (result != kIOReturnSuccess)
        return result;

    arguments->scalarOutput[0] = response;
    return kIOReturnSuccess;
}

IOReturn ALCUserClient::externalMethod(uint32_t selector,
                                       IOExternalMethodArguments *arguments,
                                       IOExternalMethodDispatch *dispatch,
                                       OSObject *target, void *reference)
{
    if (selector != kExecuteVerbSelector || provider_ == nullptr)
        return kIOReturnUnsupported;

    static const IOExternalMethodDispatch methods[] = {
        {
            methodExecuteVerb,
            kExecuteVerbInputCount,
            0,
            kExecuteVerbOutputCount,
            0
        }
    };

    dispatch = const_cast<IOExternalMethodDispatch *>(&methods[selector]);
    target = provider_;
    reference = nullptr;
    return IOUserClient::externalMethod(selector, arguments, dispatch, target, reference);
}
