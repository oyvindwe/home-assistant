"""Python Control of Nobø Hub - Nobø Energy Control."""
from __future__ import annotations

import logging
from typing import Any

from pynobo import nobo

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    PRESET_AWAY,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_NONE,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_MANUFACTURER,
    ATTR_MODE,
    ATTR_MODEL,
    ATTR_NAME,
    PRECISION_TENTHS,
    TEMP_CELSIUS,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import (
    ATTR_HARDWARE_VERSION,
    ATTR_OVERRIDE_ALLOWED,
    ATTR_SERIAL,
    ATTR_SOFTWARE_VERSION,
    ATTR_SUGGESTED_AREA,
    ATTR_TARGET_ID,
    ATTR_TARGET_TYPE,
    ATTR_TEMP_COMFORT_C,
    ATTR_TEMP_ECO_C,
    ATTR_VIA_DEVICE,
    ATTR_ZONE_ID,
    CONF_OVERRIDE_TYPE,
    DOMAIN,
    NOBO_MANUFACTURER,
    OVERRIDE_TYPE_NOW,
)

SUPPORT_FLAGS = (
    ClimateEntityFeature.PRESET_MODE | ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
)

PRESET_MODES = [PRESET_NONE, PRESET_COMFORT, PRESET_ECO, PRESET_AWAY]

MIN_TEMPERATURE = 7
MAX_TEMPERATURE = 40

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Nobø Ecohub platform from UI configuration."""

    # Setup connection with hub
    hub: nobo = hass.data[DOMAIN][config_entry.entry_id]

    override_type = (
        nobo.API.OVERRIDE_TYPE_NOW
        if config_entry.options.get(CONF_OVERRIDE_TYPE) == OVERRIDE_TYPE_NOW
        else nobo.API.OVERRIDE_TYPE_CONSTANT
    )

    # Add zones as entities
    entities: list[Entity] = [
        NoboZone(zone_id, hub, override_type) for zone_id in hub.zones
    ]

    dev_reg = await hass.helpers.device_registry.async_get_registry()
    # Register hub
    dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, hub.hub_info[ATTR_SERIAL])},
        manufacturer=NOBO_MANUFACTURER,
        name=hub.hub_info[ATTR_NAME],
        model=f"Nobø Ecohub ({hub.hub_info[ATTR_HARDWARE_VERSION]})",
        sw_version=hub.hub_info[ATTR_SOFTWARE_VERSION],
    )
    for (component_id, component) in hub.components.items():
        model: nobo.Model = component[ATTR_MODEL]
        if model.has_temp_sensor:
            # Register temperature sensor
            entities.append(NoboTemperatureSensor(component_id, hub))
        else:
            # Register other component as device without entity.
            zone_id = component[ATTR_ZONE_ID]
            zone_name = None
            if zone_id != -1:
                zone_name = hub.zones[zone_id][ATTR_NAME]
            dev_reg.async_get_or_create(
                config_entry_id=config_entry.entry_id,
                identifiers={(DOMAIN, component[ATTR_SERIAL])},
                manufacturer=NOBO_MANUFACTURER,
                name=component[ATTR_NAME],
                model=component[ATTR_MODEL].name,
                via_device=(DOMAIN, hub.hub_info[ATTR_SERIAL]),
                suggested_area=zone_name,
            )

    async_add_entities(entities, True)


class NoboZone(ClimateEntity):
    """Representation of a Nobø zone.

    A Nobø zone consists of a group of physical devices that are
    controlled as a unity.
    """

    _attr_max_temp = MAX_TEMPERATURE
    _attr_min_temp = MIN_TEMPERATURE
    _attr_precision = PRECISION_TENTHS
    _attr_preset_modes = PRESET_MODES
    # Need to poll to get preset change when in HVACMode.AUTO.
    _attr_supported_features = SUPPORT_FLAGS
    _attr_temperature_unit = TEMP_CELSIUS

    def __init__(self, zone_id, hub: nobo, override_type):
        """Initialize the climate device."""
        self._id = zone_id
        self._nobo = hub
        self._attr_unique_id = f"{hub.hub_serial}:{zone_id}"
        self._attr_name = hub.zones[self._id][ATTR_NAME]
        self._attr_has_entity_name = True
        self._attr_hvac_mode = HVACMode.AUTO
        self._attr_hvac_modes = [HVACMode.HEAT, HVACMode.AUTO]
        self._override_type = override_type

    async def async_added_to_hass(self) -> None:
        """Register callback from hub."""
        self._nobo.register_callback(self._after_update)

    async def async_will_remove_from_hass(self) -> None:
        """Deregister callback from hub."""
        self._nobo.deregister_callback(self._after_update)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target HVAC mode, if it's supported."""
        if hvac_mode not in self.hvac_modes:
            raise ValueError(
                f"Zone {self._id} '{self._attr_name}' called with unsupported HVAC mode '{hvac_mode}'"
            )
        if hvac_mode == HVACMode.AUTO:
            await self.async_set_preset_mode(PRESET_NONE)
        elif hvac_mode == HVACMode.HEAT:
            await self.async_set_preset_mode(PRESET_COMFORT)
        self._attr_hvac_mode = hvac_mode

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new zone override."""
        if self._nobo.zones[self._id][ATTR_OVERRIDE_ALLOWED] != "1":
            return
        if preset_mode == PRESET_ECO:
            mode = nobo.API.OVERRIDE_MODE_ECO
        elif preset_mode == PRESET_AWAY:
            mode = nobo.API.OVERRIDE_MODE_AWAY
        elif preset_mode == PRESET_COMFORT:
            mode = nobo.API.OVERRIDE_MODE_COMFORT
        else:  # PRESET_NONE
            mode = nobo.API.OVERRIDE_MODE_NORMAL
        await self._nobo.async_create_override(
            mode,
            self._override_type,
            nobo.API.OVERRIDE_TARGET_ZONE,
            self._id,
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        low, high = None, None
        if ATTR_TARGET_TEMP_LOW in kwargs:
            low = round(kwargs[ATTR_TARGET_TEMP_LOW])
        if ATTR_TARGET_TEMP_HIGH in kwargs:
            high = round(kwargs[ATTR_TARGET_TEMP_HIGH])
        if low is not None:
            if high is not None:
                low = min(low, high)
            elif self.target_temperature_high is not None:
                low = min(low, int(self.target_temperature_high))
        elif high is not None and self.target_temperature_low is not None:
            high = max(high, int(self.target_temperature_low))
        await self._nobo.async_update_zone(
            self._id, temp_comfort_c=high, temp_eco_c=low
        )

    async def async_update(self) -> None:
        """Fetch new state data for this zone."""
        self._read_state()

    @callback
    def _read_state(self) -> None:
        """Read the current state from the hub. These are only local calls."""
        state = self._nobo.get_current_zone_mode(self._id)
        self._attr_hvac_mode = HVACMode.AUTO
        self._attr_preset_mode = PRESET_NONE

        if state == nobo.API.NAME_OFF:
            self._attr_hvac_mode = HVACMode.OFF
        elif state == nobo.API.NAME_AWAY:
            self._attr_preset_mode = PRESET_AWAY
        elif state == nobo.API.NAME_ECO:
            self._attr_preset_mode = PRESET_ECO
        elif state == nobo.API.NAME_COMFORT:
            self._attr_preset_mode = PRESET_COMFORT

        if self._nobo.zones[self._id][ATTR_OVERRIDE_ALLOWED] == "1":
            for override in self._nobo.overrides:
                if self._nobo.overrides[override][ATTR_MODE] == "0":
                    continue  # "normal" overrides
                if (
                    self._nobo.overrides[override][ATTR_TARGET_TYPE]
                    == nobo.API.OVERRIDE_TARGET_ZONE
                    and self._nobo.overrides[override][ATTR_TARGET_ID] == self._id
                ):
                    self._attr_hvac_mode = HVACMode.HEAT
                    break

        current_temperature = self._nobo.get_current_zone_temperature(self._id)
        self._attr_current_temperature = (
            None if current_temperature is None else float(current_temperature)
        )
        self._attr_target_temperature_high = int(
            self._nobo.zones[self._id][ATTR_TEMP_COMFORT_C]
        )
        self._attr_target_temperature_low = int(
            self._nobo.zones[self._id][ATTR_TEMP_ECO_C]
        )

    @callback
    def _after_update(self, hub):
        self._read_state()
        self.async_write_ha_state()


class NoboTemperatureSensor(SensorEntity):
    """A Nobø device with a temperature sensor."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = TEMP_CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, component_id: str, hub: nobo) -> None:
        """Initialize the temperature sensor."""
        self._temperature: StateType = None
        self._id = component_id
        self._nobo = hub
        component = hub.components[self._id]
        self._attr_unique_id = component[ATTR_SERIAL]
        self._attr_name = component[ATTR_NAME]
        self._attr_has_entity_name = True
        self._attr_device_info: DeviceInfo = {
            ATTR_NAME: component[ATTR_NAME],
            ATTR_MANUFACTURER: NOBO_MANUFACTURER,
            ATTR_MODEL: component[ATTR_MODEL].name,
            ATTR_VIA_DEVICE: (DOMAIN, hub.hub_info[ATTR_SERIAL]),
        }
        zone_id = component[ATTR_ZONE_ID]
        if zone_id != -1:
            self._attr_device_info[ATTR_SUGGESTED_AREA] = hub.zones[zone_id][ATTR_NAME]

    async def async_added_to_hass(self) -> None:
        """Register callback from hub."""
        self._nobo.register_callback(self._after_update)

    async def async_will_remove_from_hass(self) -> None:
        """Deregister callback from hub."""
        self._nobo.deregister_callback(self._after_update)

    @callback
    def _after_update(self, hub) -> None:
        self._temperature = hub.get_current_component_temperature(self._id)
        self.async_write_ha_state()

    @property
    def native_value(self) -> StateType:
        """Return the current temperature."""
        return self._temperature
