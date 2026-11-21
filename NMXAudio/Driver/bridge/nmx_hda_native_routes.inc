/* Called by the FreeBSD HDA association builder before native discovery. */
static bool
nmx_hda_is_output_pin(uint32_t config)
{
	switch (config & HDA_CONFIG_DEFAULTCONF_DEVICE_MASK) {
	case HDA_CONFIG_DEFAULTCONF_DEVICE_LINE_OUT:
	case HDA_CONFIG_DEFAULTCONF_DEVICE_SPEAKER:
	case HDA_CONFIG_DEFAULTCONF_DEVICE_HP_OUT:
	case HDA_CONFIG_DEFAULTCONF_DEVICE_SPDIF_OUT:
	case HDA_CONFIG_DEFAULTCONF_DEVICE_DIGITAL_OTHER_OUT:
		return true;
	default:
		return false;
	}
}

/* FreeBSD requires each numbered association to have a single direction. */
static void
nmx_hda_parse_profile_associations(struct hdaa_devinfo *devinfo)
{
	if (devinfo->nmx_native_profile == NULL) {
		hdaa_audio_as_parse(devinfo);
		return;
	}
	uint16_t input = 0, output = 0;
	uint8_t replacement[16] = {0};
	uint8_t original[256] = {0};
	for (int nid = devinfo->startnode; nid < devinfo->endnode && nid < 256; ++nid) {
		struct hdaa_widget *w = hdaa_widget_get(devinfo, nid);
		if (w == NULL || !w->enable || w->type != HDA_PARAM_AUDIO_WIDGET_CAP_TYPE_PIN_COMPLEX)
			continue;
		const uint32_t config = w->wclass.pin.config;
		const int association = HDA_CONFIG_DEFAULTCONF_ASSOCIATION(config);
		if (association <= 0 || association >= 15)
			continue;
		if (nmx_hda_is_output_pin(config))
			output |= 1U << association;
		else {
			input |= 1U << association;
			original[nid] = association;
		}
	}
	uint16_t used = input | output;
	for (int association = 1; association < 15; ++association) {
		if ((input & output & (1U << association)) == 0)
			continue;
		for (int free = 1; free < 15; ++free) {
			if ((used & (1U << free)) != 0)
				continue;
			replacement[association] = free;
			used |= 1U << free;
			break;
		}
	}
	for (int nid = devinfo->startnode; nid < devinfo->endnode && nid < 256; ++nid) {
		const uint8_t association = replacement[original[nid]];
		if (association == 0)
			continue;
		struct hdaa_widget *w = hdaa_widget_get(devinfo, nid);
		w->wclass.pin.config &= ~HDA_CONFIG_DEFAULTCONF_ASSOCIATION_MASK;
		w->wclass.pin.config |= (uint32_t)association << HDA_CONFIG_DEFAULTCONF_ASSOCIATION_SHIFT;
	}
	hdaa_audio_as_parse(devinfo);
	for (int nid = devinfo->startnode; nid < devinfo->endnode && nid < 256; ++nid) {
		if (replacement[original[nid]] == 0)
			continue;
		struct hdaa_widget *w = hdaa_widget_get(devinfo, nid);
		w->wclass.pin.config &= ~HDA_CONFIG_DEFAULTCONF_ASSOCIATION_MASK;
		w->wclass.pin.config |= (uint32_t)original[nid] << HDA_CONFIG_DEFAULTCONF_ASSOCIATION_SHIFT;
	}
}

struct nmx_hda_route_binding {
	uint16_t sequences;
	uint16_t connection; /* Zero means unset, otherwise the connection index plus one. */
};

static int
nmx_hda_route_connection(struct hdaa_widget *sink, nid_t source,
	const struct nmx_hda_route_binding *binding)
{
	for (int i = 0; i < sink->nconns; ++i) {
		if (sink->connsenable[i] == 0 || sink->conns[i] != source)
			continue;
		if (sink->type == HDA_PARAM_AUDIO_WIDGET_CAP_TYPE_AUDIO_MIXER)
			return 0;
		if ((sink->selconn == -1 || sink->selconn == i) &&
		    (binding->connection == 0 || binding->connection == i + 1))
			return i + 1;
	}
	return -1;
}

static nid_t
nmx_hda_plan_path(struct hdaa_devinfo *devinfo, int association, int sequence,
	const struct nmx_native_route *route, struct nmx_hda_route_binding *plan,
	nid_t shared_converter, int duplicate_sequence)
{
	const struct hdaa_audio_as *as = &devinfo->as[association];
	const nid_t pin = as->pins[sequence];
	const bool output = as->dir == HDAA_CTL_OUT;
	const int converter_type = output ? HDA_PARAM_AUDIO_WIDGET_CAP_TYPE_AUDIO_OUTPUT :
	    HDA_PARAM_AUDIO_WIDGET_CAP_TYPE_AUDIO_INPUT;
	struct hdaa_widget *widgets[HDA_PARSE_MAXDEPTH + 1];
	int connections[HDA_PARSE_MAXDEPTH];
	const int length = route->length;
	if (length < 2 || length > HDA_PARSE_MAXDEPTH + 1)
		return 0;
	const bool reverse = route->nodes[length - 1] == pin;
	if (!reverse && route->nodes[0] != pin)
		return 0;

	for (int i = 0; i < length; ++i) {
		const uint8_t nid = route->nodes[reverse ? length - 1 - i : i];
		struct hdaa_widget *widget = hdaa_widget_get(devinfo, nid);
		if (widget == NULL || !widget->enable || widget->bindas >= 0)
			return 0;
		for (int j = 0; j < i; ++j) {
			if (widgets[j] == widget)
				return 0;
		}
		if (i == 0 && widget->type != HDA_PARAM_AUDIO_WIDGET_CAP_TYPE_PIN_COMPLEX)
			return 0;
		if (i == length - 1 && widget->type != converter_type)
			return 0;
		if (i > 0 && i < length - 1 &&
		    widget->type != HDA_PARAM_AUDIO_WIDGET_CAP_TYPE_AUDIO_MIXER &&
		    widget->type != HDA_PARAM_AUDIO_WIDGET_CAP_TYPE_AUDIO_SELECTOR)
			return 0;
		if (plan[nid].sequences != 0 && !(as->mixed && !output) &&
		    (duplicate_sequence < 0 ||
		    (plan[nid].sequences & (1U << duplicate_sequence)) == 0))
			return 0;
		widgets[i] = widget;
	}

	const nid_t converter = widgets[length - 1]->nid;
	if (shared_converter != 0 && converter != shared_converter)
		return 0;
	for (int i = 0; i < length - 1; ++i) {
		struct hdaa_widget *sink = output ? widgets[i] : widgets[i + 1];
		const nid_t source = output ? widgets[i + 1]->nid : widgets[i]->nid;
		connections[i] = nmx_hda_route_connection(sink, source, &plan[sink->nid]);
		if (connections[i] < 0)
			return 0;
	}

	for (int i = 0; i < length; ++i)
		plan[widgets[i]->nid].sequences |= 1U << sequence;
	for (int i = 0; i < length - 1; ++i) {
		struct hdaa_widget *sink = output ? widgets[i] : widgets[i + 1];
		if (sink->nconns > 1 || sink->type == HDA_PARAM_AUDIO_WIDGET_CAP_TYPE_AUDIO_SELECTOR)
			plan[sink->nid].connection = connections[i];
	}
	return converter;
}

/* Commit an association only after every pin has a validated path. */
static bool
nmx_hda_trace_profile_association(struct hdaa_devinfo *devinfo, int association)
{
	const struct nmx_native_profile *profile = devinfo->nmx_native_profile;
	if (profile == NULL || profile->route_count == 0)
		return false;

	struct hdaa_audio_as *as = &devinfo->as[association];
	if (as->pincnt == 0)
		return false;
	struct nmx_hda_route_binding plan[256] = {0};
	nid_t converters[16] = {0};
	const bool output = as->dir == HDAA_CTL_OUT;
	nid_t mixed_converter = 0;
	for (int sequence = 0; sequence < 16; ++sequence) {
		if (as->pins[sequence] == 0)
			continue;
		const int redirect = output && sequence == 15 && !as->fakeredir ? as->hpredir : -1;
		if (redirect >= 16 || (redirect >= 0 && converters[redirect] == 0))
			return false;
		const nid_t shared = redirect >= 0 ? converters[redirect] : mixed_converter;
		for (uint16_t i = 0; i < profile->route_count; ++i) {
			converters[sequence] = nmx_hda_plan_path(devinfo, association,
			    sequence, &profile->routes[i], plan, shared, redirect);
			if (converters[sequence] != 0)
				break;
		}
		if (converters[sequence] == 0)
			return false;
		if (!output && as->mixed)
			mixed_converter = converters[sequence];
	}

	for (int nid = devinfo->startnode; nid < devinfo->endnode && nid < 256; ++nid) {
		if (plan[nid].sequences == 0)
			continue;
		struct hdaa_widget *widget = hdaa_widget_get(devinfo, nid);
		widget->bindas = association;
		widget->bindseqmask = plan[nid].sequences;
		if (plan[nid].connection != 0)
			widget->selconn = plan[nid].connection - 1;
	}
	memcpy(as->dacs[0], converters, sizeof(converters));
	return true;
}
