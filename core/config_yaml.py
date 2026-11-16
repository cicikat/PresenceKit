"""Safe YAML codecs for frequently read runtime configuration.

Use LibYAML when installed; minimal installations retain the safe Python codecs.
Neither loader accepts arbitrary Python objects.
"""
import yaml

SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
SAFE_DUMPER = getattr(yaml, "CSafeDumper", yaml.SafeDumper)


def load_config_yaml(stream):
    return yaml.load(stream, Loader=SAFE_LOADER)


def dump_config_yaml(value, **options):
    return yaml.dump(value, Dumper=SAFE_DUMPER, **options)
