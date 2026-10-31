"""Defaults and validation for /data/options.json.

Under Home Assistant the Supervisor checks options.json against the `schema:`
block of config.yaml and fills in the `options:` defaults before the add-on
starts. Run standalone (plain Docker), nothing does that, so the app does it
here with the same defaults and the same rules. For a file written by the
Supervisor this changes nothing: no defaults are missing and every value
already passes.

DEFAULTS and SCHEMA must stay equal to config.yaml (tests/test_options.py
checks this).
"""
import copy
import json
import re
from typing import Any

DEFAULTS = {
    "inverter": {
        "host": "",
        "port": 502,
        "slave": 1,
        "winet_connection": False,
    },
    "additional_inverters": [],
    "mqtt": {
        "host": "",
        "port": 1883,
        "username": "",
        "password": "",
        "homeassistant": True,
    },
    "scan": {
        "delay": 5,
        "timeout": 30,
        "retries": 3,
        "level": "FULL",
        "interval": {
            "realtime": 5,
            "fast": 10,
            "medium": 60,
            "slowest": 600,
        },
    },
    "log_level": "INFO",
}

# Same notation as the Home Assistant add-on schema; a "?" suffix marks an
# optional key, a list holding one dict describes a list of such objects.
SCHEMA = {
    "inverter": {
        "host": "str",
        "port": "port",
        "slave": "int",
        "winet_connection": "bool",
    },
    "additional_inverters": [
        {
            "host": "str",
            "port": "port?",
            "slave": "int?",
            "winet_connection": "bool?",
            "name": "str?",
        }
    ],
    "mqtt": {
        "host": "str",
        "port": "port",
        "username": "str",
        "password": "password",
        "homeassistant": "bool",
    },
    "scan": {
        "delay": "int(1,15)",
        "timeout": "int(3,60)",
        "retries": "int(0,10)",
        "level": "list(BASIC|STANDARD|FULL)",
        "interval": {
            "realtime": "int(1,5)",
            "fast": "int(1,10)",
            "medium": "int(30,60)",
            "slowest": "int(100,600)",
        },
    },
    "log_level": "list(INFO|WARNING|ERROR|DEBUG)",
}

# Keys the code reads although they are not in the add-on schema.
HIDDEN_KEYS = {"log_file", "mqtt.topic", "mqtt.client_id", "scan.message_wait"}
# Inverter entries may carry arbitrary extra keys (register.py reads e.g.
# battery_max_power from them), so their unknown keys are not reported.
OPEN_SECTIONS = {"inverter", "additional_inverters"}

_RANGE = re.compile(r"^int\((-?\d+),(-?\d+)\)$")
_CHOICE = re.compile(r"^list\((.+)\)$")


class OptionsError(Exception):
    """options.json cannot be read as a configuration at all."""


def load(path) -> tuple[dict, list[str]]:
    """Reads options.json and fills in the add-on defaults for missing keys.
    Returns the merged config and the dotted names of the filled-in keys."""
    try:
        with open(path) as f:
            raw = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        raise OptionsError(f'{path} is not valid JSON: {err}') from err
    if not isinstance(raw, dict):
        raise OptionsError(f'{path} must contain a JSON object ({{...}}), found {_type_name(raw)}')
    filled: list[str] = []
    return _merge(raw, DEFAULTS, '', filled), filled


def _merge(given: dict, defaults: dict, prefix: str, filled: list) -> dict:
    result = dict(given)
    for key, default in defaults.items():
        name = prefix + key
        if key not in result:
            result[key] = copy.deepcopy(default)
            filled.extend(_leaves(default, name))
        elif isinstance(default, dict) and isinstance(result[key], dict):
            result[key] = _merge(result[key], default, name + '.', filled)
        # Lists (additional_inverters) and values of the wrong type are taken
        # as given; validate() reports the latter.
    return result


def _leaves(value: Any, name: str) -> list:
    if isinstance(value, dict) and value:
        return [leaf for key, sub in value.items() for leaf in _leaves(sub, f'{name}.{key}')]
    return [name]


def validate(config: dict) -> list[str]:
    """Checks the config against SCHEMA. Returns one message per problem.
    Values of `list(...)` options are matched in any case and normalised in
    place to the spelling of the schema (e.g. "full" -> "FULL")."""
    errors: list[str] = []
    _check_dict(config, SCHEMA, '', errors)
    mqtt = config.get('mqtt')
    if isinstance(mqtt, dict) and isinstance(mqtt.get('host'), str) and not mqtt['host'].strip():
        errors.append('mqtt.host: not set')
    return errors


def _check_dict(values: dict, schema: dict, prefix: str, errors: list) -> None:
    for key, rule in schema.items():
        name = prefix + key
        optional = isinstance(rule, str) and rule.endswith('?')
        if key not in values or (optional and values[key] is None):
            if not optional:
                errors.append(f'{name}: missing')
            continue
        value = values[key]
        if isinstance(rule, dict):
            if not isinstance(value, dict):
                errors.append(f'{name}: must be an object, found {_type_name(value)}')
            else:
                _check_dict(value, rule, name + '.', errors)
        elif isinstance(rule, list):
            if not isinstance(value, list):
                errors.append(f'{name}: must be a list, found {_type_name(value)}')
                continue
            for index, item in enumerate(value):
                item_name = f'{name}[{index}]'
                if not isinstance(item, dict):
                    errors.append(f'{item_name}: must be an object, found {_type_name(item)}')
                else:
                    _check_dict(item, rule[0], item_name + '.', errors)
        else:
            error, normalised = _check_value(value, rule.rstrip('?'))
            if error:
                errors.append(f'{name}: {error}')
            elif normalised is not value:
                values[key] = normalised


def _check_value(value: Any, rule: str) -> tuple[str | None, Any]:
    """Returns (error or None, value to store)."""
    # Never show a password value in the log.
    shown = 'value' if rule == 'password' else json.dumps(value)
    if rule in ('str', 'password'):
        return (None, value) if isinstance(value, str) else (f'{shown} must be a string', value)
    if rule == 'bool':
        return (None, value) if isinstance(value, bool) else (f'{shown} must be true or false', value)
    if rule in ('int', 'port') or _RANGE.match(rule):
        if not isinstance(value, int) or isinstance(value, bool):
            return f'{shown} must be an integer', value
        low, high = (1, 65535) if rule == 'port' else (None, None)
        match = _RANGE.match(rule)
        if match:
            low, high = int(match.group(1)), int(match.group(2))
        if low is not None and not low <= value <= high:
            return f'{shown} is out of range (allowed {low}-{high})', value
        return None, value
    match = _CHOICE.match(rule)
    if match:
        choices = match.group(1).split('|')
        if isinstance(value, str):
            for choice in choices:
                if value.strip().upper() == choice.upper():
                    return None, (value if value == choice else choice)
        return f'{shown} is not one of {", ".join(choices)}', value
    raise ValueError(f'unsupported schema type: {rule}')


def unknown_keys(config: dict) -> list[str]:
    """Dotted names of keys that are neither in SCHEMA nor read by the code
    (HIDDEN_KEYS), usually typos. Inverter entries are not checked."""
    found: list[str] = []
    _find_unknown(config, SCHEMA, '', found)
    return found


def _find_unknown(values: dict, schema: dict, prefix: str, found: list) -> None:
    for key, value in values.items():
        name = prefix + key
        if key not in schema:
            if name not in HIDDEN_KEYS:
                found.append(name)
        elif name not in OPEN_SECTIONS and isinstance(schema[key], dict) and isinstance(value, dict):
            _find_unknown(value, schema[key], name + '.', found)


def _type_name(value: Any) -> str:
    if value is None:
        return 'null'
    return {bool: 'true/false', int: 'a number', float: 'a number', str: 'a string',
            list: 'a list', dict: 'an object'}.get(type(value), type(value).__name__)
