"""Constants for the Nobø Ecohub integration."""

DOMAIN = "nobo_hub"

CONF_SERIAL = "serial"

HUB = "hub"
UNSUBSCRIBE = "unsubscribe"


class ComponentType:
    """Represents a Nobø component type."""

    def __init__(
        self,
        component_type: str,
        name: str,
        description: str,
        set_comfort: bool = True,
        set_eco: bool = True,
    ) -> None:
        """Create a Nobæ componenty type."""
        self.component_type = component_type
        self.name = name
        self.description = description
        self.set_comfort = set_comfort
        self.set_eco = set_eco


COMPONENT_TYPES = {
    "120": ComponentType("129", "RS-700", "Switch", False, False),
    "160": ComponentType("160", "RDC-700", "Heater", False, False),
    "168": ComponentType("168", "NCU-2R", "Heater"),
    "182": ComponentType("182", "R80 RSC 700", "Heater", False),
    "184": ComponentType("184", "NCU-1R", "Heater", False),
    "186": ComponentType("186", "NTD-4R", "Heater"),
    "192": ComponentType("192", "TXF", "Heater"),
    "198": ComponentType("198", "NCU-ER", "Heater"),
    "200": ComponentType("200", "TRB36 700", "Floor", False, False),
    "210": ComponentType("200", "NTB-2R", "Floor"),
    "234": ComponentType(
        "234", "Nobø Switch", "Control switch", False, False
    ),  # Reports current temperature
}
