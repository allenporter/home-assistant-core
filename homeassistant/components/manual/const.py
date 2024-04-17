"""Constants for the manual component."""

import datetime

DOMAIN = "manual"

CONF_CODE_ARM_REQUIRED = "code_arm_required"

DEFAULT_DELAY_TIME = datetime.timedelta(seconds=60)
DEFAULT_ARMING_TIME = datetime.timedelta(seconds=60)
DEFAULT_TRIGGER_TIME = datetime.timedelta(seconds=120)
DEFAULT_DISARM_AFTER_TRIGGER = False
