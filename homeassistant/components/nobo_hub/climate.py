"""Python Control of Nobø Hub - Nobø Energy Control."""
from __future__ import annotations

import asyncio
import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    HVAC_MODE_AUTO,
    HVAC_MODE_HEAT,
    HVAC_MODE_OFF,
    PRESET_AWAY,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_NONE,
    SUPPORT_PRESET_MODE,
    SUPPORT_TARGET_TEMPERATURE_RANGE,
)
from homeassistant.const import (
    CONF_COMMAND_OFF,
    CONF_COMMAND_ON,
    CONF_HOST,
    CONF_IP_ADDRESS,
    EVENT_HOMEASSISTANT_STOP,
    PRECISION_TENTHS,
    TEMP_CELSIUS,
)
from homeassistant.core import HomeAssistant, callback
import homeassistant.helpers.config_validation as cv
import homeassistant.util.dt as dt_util
from pynobo import nobo

from .const import CONF_SERIAL, DOMAIN

SUPPORT_FLAGS = SUPPORT_PRESET_MODE | SUPPORT_TARGET_TEMPERATURE_RANGE

PRESET_MODES = [PRESET_NONE, PRESET_COMFORT, PRESET_ECO, PRESET_AWAY]

HVAC_MODES = [HVAC_MODE_OFF, HVAC_MODE_HEAT, HVAC_MODE_AUTO]
HVAC_MODES_WITHOUT_OFF = [HVAC_MODE_HEAT, HVAC_MODE_AUTO]

MIN_TEMPERATURE = 7
MAX_TEMPERATURE = 40

_LOGGER = logging.getLogger(__name__)

_ZONE_NORMAL_WEEK_LIST_SCHEMA = vol.Schema({cv.string: cv.string})

# For backwards compatibility of HACS version.
PLATFORM_SCHEMA = cv.PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_IP_ADDRESS, default="discover"): cv.string,
        vol.Optional(CONF_COMMAND_OFF, default=""): cv.string,
        vol.Optional(CONF_COMMAND_ON, default={}): _ZONE_NORMAL_WEEK_LIST_SCHEMA,
    }
)


async def async_setup_platform(
    hass: HomeAssistant, config, async_add_entities, discovery_info=None
):
    """Set up the Nobø Ecohub platform from configuration.yaml."""

    serial = config.get(CONF_HOST)
    config[CONF_SERIAL] = serial
    ip = config.get(CONF_IP_ADDRESS)

    if ip == "discover":
        _LOGGER.info("discovering and connecting to %s", serial)
        hub = nobo(serial=serial)
    else:
        _LOGGER.info("connecting to %s:%s", ip, serial)
        hub = nobo(serial=serial, ip=ip, discover=False)
    await _setup(hass, config, async_add_entities, hub)


async def async_setup_entry(
    hass: HomeAssistant, config_entry: config_entries.ConfigEntry, async_add_devices
):
    """Set up the Nobø Ecohub platform from UI configuration."""

    # Setup connection with hub
    hub = hass.data[DOMAIN][config_entry.entry_id]
    await _setup(hass, config_entry, async_add_devices, hub)


async def _setup(
    hass: HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_devices,
    hub: nobo,
):
    await hub.start()

    # Find OFF command (week profile) to use for all zones:
    command_off_name = config_entry.data.get(CONF_COMMAND_OFF)
    command_on_by_id: dict[str, str] = {}  # By default, nothing can be turned on
    if command_off_name is None or command_off_name == "":
        _LOGGER.debug(
            "Not possible to turn off (or on) any zone, because OFF week profile was not specified"
        )
        command_off_id = None
    else:
        command_off_id = _get_id_from_name(command_off_name, hub.week_profiles)
        if command_off_id == "" or command_off_id is None:
            _LOGGER.warning(
                "Can not turn off (or on) any zone, because week profile '%s' was not found",
                command_off_name,
            )
        else:
            _LOGGER.debug(
                "To turn off any heater, week profile %s '%s' will be used",
                command_off_id,
                command_off_name,
            )

            # Find ON command (week profile) for the different zones:
            command_on_dict = config_entry.data.get(CONF_COMMAND_ON)
            if command_on_dict is None or command_on_dict.keys().__len__ == 0:
                _LOGGER.warning(
                    "Not possible to turn on any zone, because ON week profile was not specified"
                )
            else:
                for zone_id, zone in hub.zones.values():
                    zone_name = zone["name"].replace("\xa0", " ")
                    if zone_name in command_on_dict:
                        command_on_name = command_on_dict[zone_name]
                        command_on_id = _get_id_from_name(
                            command_on_name, hub.week_profiles
                        )
                        if command_on_id is None or command_on_id == "":
                            _LOGGER.warning(
                                "Can not turn on (or off) zone '%s', because the week profile '%s' was not found",
                                zone_name,
                                command_on_name,
                            )
                        else:
                            _LOGGER.debug(
                                "To turn on heater %s '%s', week profile %s '%s' will be used",
                                zone_id,
                                zone_name,
                                command_on_id,
                                command_on_name,
                            )
                            command_on_by_id[zone_id] = command_on_id

    # Add devices
    async_add_devices(
        NoboZone(zones, hub, command_off_id, command_on_by_id.get(zones))
        for zones in hub.zones
    )
    _LOGGER.info("component is up and running on %s:%s", hub.hub_ip, hub.hub_serial)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, hub.stop)

    return True


def _get_id_from_name(name, dictionary):
    for key in dictionary.keys():
        # Replace unicode non-breaking space (used in Nobø Ecohub) with space
        if dictionary[key]["name"].replace("\xa0", " ") == name:
            return key
    return None


class NoboZone(ClimateEntity):
    """Representation of a Nobø zone. A Nobø zone consists of a group of physical devices that are controlled as a unity."""

    def __init__(self, id, hub: nobo, command_off_id, command_on_id):
        """Initialize the climate device."""
        self._id = id
        self._nobo = hub
        self._unique_id = hub.hub_serial + ":" + id
        self._name = self._nobo.zones[self._id]["name"]
        self._current_mode = HVAC_MODE_AUTO
        self._command_off_id = command_off_id
        self._command_on_id = command_on_id

        # Register for callbacks before initial update to avoid race condition.
        self._nobo.register_callback(self._after_update)
        self.update()

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self._unique_id

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return SUPPORT_FLAGS

    @property
    def should_poll(self):
        """Return the polling state."""
        return False

    @property
    def name(self):
        """Return the name of the climate device."""
        return self._name

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return TEMP_CELSIUS

    @property
    def precision(self):
        """Return the precision of the system."""
        return PRECISION_TENTHS  # PRECISION_WHOLE

    @property
    def min_temp(self):
        """Return the minimum temperature."""
        return MIN_TEMPERATURE

    @property
    def max_temp(self):
        """Return the maximum temperature."""
        return MAX_TEMPERATURE

    @property
    def target_temperature_high(self):
        """Return the highbound target temperature we try to reach."""
        return self._target_temperature_high

    @property
    def target_temperature_low(self):
        """Return the lowbound target temperature we try to reach."""
        return self._target_temperature_low

    @property
    def hvac_modes(self):
        """Return the list of available operation modes."""
        # Only enable off-command if on- and off-command exists for this zone:
        if self.can_turn_off():
            return HVAC_MODES
        else:
            return HVAC_MODES_WITHOUT_OFF

    @property
    def hvac_mode(self):
        """Return current operation HVAC Mode."""
        return self._current_mode

    @property
    def preset_mode(self):
        """Return current preset mode."""
        return self._current_operation

    @property
    def preset_modes(self):
        """Return the preset modes, comfort, away etc."""
        return PRESET_MODES

    @property
    def current_temperature(self):
        """Return the current temperature."""
        if self._current_temperature is not None:
            return float(self._current_temperature)
        return None

    async def async_set_hvac_mode(self, hvac_mode):
        """Set HVAC mode to comfort(HEAT) or back to normal(AUTO)."""
        if hvac_mode == HVAC_MODE_AUTO:
            await self.async_set_preset_mode(PRESET_NONE)
            self._current_mode = hvac_mode
        elif hvac_mode == HVAC_MODE_HEAT:
            await self.async_set_preset_mode(PRESET_COMFORT)
            self._current_mode = hvac_mode

        if self.can_turn_off():
            if hvac_mode == HVAC_MODE_OFF:
                await self.async_set_preset_mode(PRESET_NONE)
                self._current_mode = hvac_mode
                await self._nobo.async_update_zone(
                    self._id, week_profile_id=self._command_off_id
                )  # Change week profile to OFF
                _LOGGER.debug(
                    "Turned off heater %s '%s' by switching to week profile %s",
                    self._id,
                    self._name,
                    self._command_off_id,
                )
            else:
                await self._nobo.async_update_zone(
                    self._id, week_profile_id=self._command_on_id
                )  # Change week profile to normal for this zone
                _LOGGER.debug(
                    "Turned on heater %s '%s' by switching to week profile %s",
                    self._id,
                    self._name,
                    self._command_on_id,
                )
            # When switching between AUTO and OFF an immediate update does not work (the Nobø API seems to answer with old values), but it works if we add a short delay:
            await asyncio.sleep(0.5)
        elif hvac_mode == HVAC_MODE_OFF:
            _LOGGER.error(
                "User tried to turn off zone %s '%s', but this is not configured so this should be impossible.",
                self._id,
                self._name,
            )

    def can_turn_off(self):
        """Return true if heater can turn off and on."""
        return self._command_on_id is not None and self._command_off_id is not None

    async def async_set_preset_mode(self, preset_mode):
        """Set new zone override."""
        if self._nobo.zones[self._id]["override_allowed"] == "1":
            if preset_mode == PRESET_ECO:
                mode = self._nobo.API.OVERRIDE_MODE_ECO
            elif preset_mode == PRESET_AWAY:
                mode = self._nobo.API.OVERRIDE_MODE_AWAY
            elif preset_mode == PRESET_COMFORT:
                mode = self._nobo.API.OVERRIDE_MODE_COMFORT
            else:  # PRESET_NONE
                mode = self._nobo.API.OVERRIDE_MODE_NORMAL
            await self._nobo.async_create_override(
                mode,
                self._nobo.API.OVERRIDE_TYPE_CONSTANT,
                self._nobo.API.OVERRIDE_TARGET_ZONE,
                self._id,
            )
            # TODO: override to program if new operation mode == current week profile status

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        low = int(kwargs.get(ATTR_TARGET_TEMP_LOW))
        high = int(kwargs.get(ATTR_TARGET_TEMP_HIGH))
        if low > int(self._nobo.zones[self._id]["temp_comfort_c"]):
            low = int(self._nobo.zones[self._id]["temp_comfort_c"])
        if high < int(self._nobo.zones[self._id]["temp_eco_c"]):
            high = int(self._nobo.zones[self._id]["temp_eco_c"])
        await self._nobo.async_update_zone(
            self._id, temp_comfort_c=high, temp_eco_c=low
        )

    @callback
    def update(self):
        """Fetch new state data for this zone."""
        state = self._nobo.get_current_zone_mode(
            self._id, dt_util.as_local(dt_util.now())
        )
        self._current_mode = HVAC_MODE_AUTO
        self._current_operation = PRESET_NONE

        if state == self._nobo.API.NAME_OFF:
            self._current_mode = HVAC_MODE_OFF
        elif state == self._nobo.API.NAME_AWAY:
            self._current_operation = PRESET_AWAY
        elif state == self._nobo.API.NAME_ECO:
            self._current_operation = PRESET_ECO
        elif state == self._nobo.API.NAME_COMFORT:
            self._current_operation = PRESET_COMFORT

        if self._nobo.zones[self._id]["override_allowed"] == "1":
            for o in self._nobo.overrides:
                if self._nobo.overrides[o]["mode"] == "0":
                    continue  # "normal" overrides
                elif (
                    self._nobo.overrides[o]["target_type"]
                    == self._nobo.API.OVERRIDE_TARGET_ZONE
                ):
                    if self._nobo.overrides[o]["target_id"] == self._id:
                        self._current_mode = HVAC_MODE_HEAT

        self._current_temperature = self._nobo.get_current_zone_temperature(self._id)
        self._target_temperature_high = int(
            self._nobo.zones[self._id]["temp_comfort_c"]
        )
        self._target_temperature_low = int(self._nobo.zones[self._id]["temp_eco_c"])

    @callback
    def _after_update(self, hub):
        self.update()
        self.async_schedule_update_ha_state()
