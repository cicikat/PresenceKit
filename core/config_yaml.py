"""Safe YAML codecs for frequently read runtime configuration.

Use LibYAML when installed; minimal installations retain the safe Python codecs.
Neither loader accepts arbitrary Python objects.
"""
import yaml

SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
SAFE_DUMPER = getattr(yaml, "CSafeDumper", yaml.SafeDumper)


class ConfigMappingError(ValueError):
    """The runtime configuration document has an unsupported root."""


def load_config_yaml(stream):
    return yaml.load(stream, Loader=SAFE_LOADER)


def load_config_mapping(stream) -> dict:
    value = load_config_yaml(stream) or {}
    if not isinstance(value, dict):
        raise ConfigMappingError("configuration root must be a mapping")
    return value


def dump_config_yaml(value, **options):
    return yaml.dump(value, Dumper=SAFE_DUMPER, **options)
