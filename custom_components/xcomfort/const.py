DEFAULT_NAME = "XComfort SHC"
DOMAIN = "xcomfort"
VERSION = "1.3.17"

# Older versions gave every controller's battery summary this one unique id, so
# with several SHCs only the first got the sensor.
LEGACY_BATTERY_SUMMARY_ID = "xcomfort_batteries"


def battery_summary_unique_id(config_entry):
    """Unique id of an entry's battery summary sensor, one per controller."""
    return LEGACY_BATTERY_SUMMARY_ID + "_" + (config_entry.unique_id or config_entry.entry_id)
