/* Send AppleALC-style verbs through the attached native HD Audio controller. */
#include <dev/sound/pcm/sound.h>
#include <dev/sound/pci/hda/hdac.h>
#include <dev/sound/pci/hda/hda_reg.h>
#include "hdac_if.h"

/* nmx_port_attach_pci returns the PCI container above the hdac driver. */
static int
find_hdac(device_t container, device_t *controller)
{
	device_t *children;
	int count;
	int error = device_get_children(container, &children, &count);
	if (error != 0)
		return error;
	for (int i = 0; i < count; ++i) {
		const char *name = device_get_name(children[i]);
		if (device_is_attached(children[i]) && name != NULL &&
		    strcmp(name, "hdac") == 0) {
			*controller = children[i];
			break;
		}
	}
	if (children != NULL)
		free(children, M_TEMP);
	return *controller == NULL ? ENXIO : 0;
}

static int
has_audio_function(device_t codec, bool *found)
{
	device_t *functions;
	int count;
	int error = device_get_children(codec, &functions, &count);
	if (error != 0)
		return error;
	for (int i = 0; i < count; ++i) {
		if (device_is_attached(functions[i]) &&
		    hda_get_node_type(functions[i]) == HDA_PARAM_FCT_GRP_TYPE_NODE_TYPE_AUDIO) {
			*found = true;
			break;
		}
	}
	if (functions != NULL)
		free(functions, M_TEMP);
	return 0;
}

/* The front end holds its controller lifecycle lock throughout this call. */
int
nmx_hda_codec_verb(void *container, uint16_t nid, uint16_t verb,
    uint16_t param, uint32_t *response)
{
	if (nid > 0xff || verb > 0xfff || (verb > 0xf && param > 0xff))
		return EINVAL;
	uint32_t command = verb <= 0xf ? HDA_CMD_4BIT(0, nid, verb, param) :
	    HDA_CMD_12BIT(0, nid, verb, param);

	device_t dev = NULL;
	int error = find_hdac(container, &dev);
	if (error != 0)
		return error;
	device_t *codecs;
	int count;
	error = device_get_children(dev, &codecs, &count);
	if (error != 0)
		return error;
	device_t codec = NULL;
	for (int i = 0; i < count; ++i) {
		if (!device_is_attached(codecs[i]))
			continue;
		bool audio = false;
		error = has_audio_function(codecs[i], &audio);
		if (error != 0)
			break;
		if (!audio)
			continue;
		if (codec != NULL) {
			error = EEXIST;
			break;
		}
		codec = codecs[i];
	}
	if (codecs != NULL)
		free(codecs, M_TEMP);
	if (error != 0)
		return error;
	if (codec == NULL)
		return ENXIO;

	struct mtx *lock = HDAC_GET_MTX(dev, codec);
	mtx_lock(lock);
	*response = HDAC_CODEC_COMMAND(dev, codec, command);
	mtx_unlock(lock);
	return *response == HDA_INVALID ? EIO : 0;
}
