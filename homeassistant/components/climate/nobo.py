"""
Support for Nobø Eco Hub.

Uses Nobø Python API from https://github.com/echoromeo/pynobo
"""

import logging
import voluptuous as vol

from homeassistant.components.climate import (
    ClimateDevice, DOMAIN, PLATFORM_SCHEMA)
from homeassistant.const import (CONF_IP_ADDRESS)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity import Entity

from nobo import nobo

REQUIREMENTS = ['nobo==0.0.1']

_LOGGER = logging.getLogger(__name__)

CONF_SERIAL = 'serial'

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_SERIAL): cv.string,
    vol.Optional(CONF_IP_ADDRESS): cv.string,
})

OVERRIDE_MODES = {
    nobo.API.OVERRIDE_MODE_NORMAL: 'normal',
    nobo.API.OVERRIDE_MODE_COMFORT: 'comfort',
    nobo.API.OVERRIDE_MODE_ECO: 'eco',
    nobo.API.OVERRIDE_MODE_AWAY: 'away',
}

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    hub = nobo(config.get(CONF_SERIAL), config.get(CONF_IP_ADDRESS))
    zones = {id: NoboZone(hub, zone) for id, zone in hub.zones.items()}

    for component in hub.components.values():
        zones[component['zone_id']].add_component(NoboComponent(component))

    devices = list(zones.values()) + [NoboHub(hub)]
    async_add_entities(devices)

class NoboHub(Entity):
    def __init__(self, hub):
        self._hub = hub
        self._name = hub.hub_info['name']
        self._variables = {}
        if (hub.hub_info['override_id'] == nobo.API.OVERRIDE_ID_NONE):
            self._state = nobo.API.OVERRIDE_MODE_NORMAL
        else:
            self._state = hub.overrides[hub.hub_info['override_id']]['mode']

    @property
    def name(self):
        return self._name

    @property
    def state(self):
        return OVERRIDE_MODES[self._state]

    @property
    def state_attributes(self):
        """Return the state attributes."""
        attr = self._variables.copy()
        return attr

    def _update_variables(self, now):
        """Retrieve all variable data and update nobo variable states."""
        variables = self._hub.hub_info
        if variables is None:
            return

        state_change = False
        for key, value in variables.items():
            if key in self._variables and value == self._variables[key]:
                continue

            state_change = True
            self._variables.update({key: value})

        if state_change:
            self.schedule_update_ha_state()


class NoboZone(Entity):
    # Represents a zone. A zone consists of a weekly program and set of components.
    # Also, state of the zone may be overridden.
    #
    # COMFORT
    # ECO
    # AWAY
    # OFF
    def __init__(self, hub, zone):
        self._hub = hub
        self._zone = zone
        self._components = []
        self._name = zone['name']
        self._variables = {}
        self._state = hub.get_current_zone_mode(zone_id=zone['zone_id'])

    def add_component(self, component):
        self._components.append(component)

    @property
    def name(self):
        return self._name

    @property
    def state(self):
        return self._state

    @property
    def components(self):
        return self._components

class NoboComponent(Entity):
    # Represents an component with four possible states:
    # COMFORT
    # ECO
    # AWAY
    # OFF
    #
    # Types:
    # 160: Electric radiator
    # 200: Underfloor heat controller
    def __init__(self, component):
        self._component = component
        self._name = component['name']
        self._variables = {}
        self._state = None
        self._variables['serial'] = component['serial']

    @property
    def name(self):
        return self._name

    @property
    def state(self):
        return self._state
