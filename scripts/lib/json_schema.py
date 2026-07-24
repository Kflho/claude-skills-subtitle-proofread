#!/usr/bin/env python3
"""Lightweight JSON Schema validation for pipeline data contracts.

No external dependency required. Falls back to basic key/type checking
if jsonschema is not installed.

Usage:
    from lib.json_schema import validate

    data = json.load(open('temp/scans/findings.json'))
    validate(data, 'findings')  # raises ValidationError on failure
"""

import json
import os
import sys

_SCHEMA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'schemas')


class ValidationError(Exception):
    """Schema validation failure."""
    def __init__(self, schema_name, errors):
        self.schema_name = schema_name
        self.errors = errors
        msg = f"{schema_name}: {len(errors)} error(s)\n" + "\n".join(f"  - {e}" for e in errors)
        super().__init__(msg)


def _load_schema(name):
    """Load a .schema.json file from the schemas/ directory."""
    path = os.path.join(_SCHEMA_DIR, f'{name}.schema.json')
    if not os.path.exists(path):
        raise FileNotFoundError(f"Schema not found: {path}")
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _check_type(instance, expected):
    """Check a single type. Returns True if instance matches."""
    if expected == 'string':
        return isinstance(instance, str)
    elif expected == 'number':
        return isinstance(instance, (int, float))
    elif expected == 'integer':
        return isinstance(instance, int) and not isinstance(instance, bool)
    elif expected == 'boolean':
        return isinstance(instance, bool)
    elif expected == 'array':
        return isinstance(instance, list)
    elif expected == 'object':
        return isinstance(instance, dict)
    elif expected == 'null':
        return instance is None
    return True  # unknown type → pass


def _validate_type(instance, expected, path='$'):
    """Check that instance matches expected JSON Schema type(s)."""
    errors = []

    # Support type arrays: ["string", "null"]
    if isinstance(expected, list):
        if not any(_check_type(instance, t) for t in expected):
            type_names = '|'.join(expected)
            errors.append(f"{path}: expected {type_names}, got {type(instance).__name__}")
        return errors

    if not _check_type(instance, expected):
        errors.append(f"{path}: expected {expected}, got {type(instance).__name__}")

    return errors


def _validate_object(instance, schema, path='$'):
    """Validate an object against a JSON Schema object definition."""
    errors = []

    if schema.get('type') != 'object':
        e = _validate_type(instance, schema.get('type', 'any'), path)
        return e

    if not isinstance(instance, dict):
        return [f"{path}: expected object, got {type(instance).__name__}"]

    # Required properties
    for key in schema.get('required', []):
        if key not in instance:
            errors.append(f"{path}.{key}: missing required property")

    # Properties
    for key, prop_schema in schema.get('properties', {}).items():
        if key not in instance:
            continue
        val = instance[key]
        p = f'{path}.{key}'

        prop_type = prop_schema.get('type')
        if prop_type:
            errors.extend(_validate_type(val, prop_type, p))

        # Enum constraint
        if 'enum' in prop_schema and val not in prop_schema['enum']:
            errors.append(f"{p}: value {val!r} not in enum {prop_schema['enum']}")

        # Object sub-validation
        if prop_type == 'object' and isinstance(val, dict) and 'properties' in prop_schema:
            errors.extend(_validate_object(val, prop_schema, p))

        # Array items
        if prop_type == 'array' and isinstance(val, list) and 'items' in prop_schema:
            items_schema = prop_schema['items']
            for i, item in enumerate(val):
                errors.extend(_validate_object(item, items_schema, f'{p}[{i}]'))

    # Additional properties warning (info only, not an error)
    if not schema.get('additionalProperties', True):
        extra = set(instance.keys()) - set(schema.get('properties', {}).keys())
        for key in extra:
            errors.append(f"{path}.{key}: unexpected property (schema allows only known keys)")

    return errors


def validate(instance, schema_name):
    """Validate data against a named schema.

    Args:
        instance: The JSON data (dict or list).
        schema_name: Name of the schema file (without .schema.json extension).

    Raises:
        ValidationError: If validation fails.
        FileNotFoundError: If the schema file doesn't exist.

    Tries jsonschema first; falls back to basic validation if not installed.
    """
    schema = _load_schema(schema_name)
    # Resolve $ref references
    schema = _resolve_refs(schema, schema)

    # Try jsonschema if installed
    try:
        import jsonschema
        validator = jsonschema.Draft7Validator(schema)
        errors = list(validator.iter_errors(instance))
        if errors:
            raise ValidationError(schema_name, [e.message for e in errors])
        return
    except ImportError:
        pass

    # Fallback: basic validation
    if isinstance(instance, list) and schema.get('type') == 'array':
        errors = []
        items_schema = schema.get('items', {})
        for i, item in enumerate(instance):
            errors.extend(_validate_object(item, items_schema, f'$[{i}]'))
        if errors:
            raise ValidationError(schema_name, errors)
    else:
        errors = _validate_object(instance, schema)
        if errors:
            raise ValidationError(schema_name, errors)


def _resolve_refs(schema, root):
    """Resolve $ref references in a schema (one level for items)."""
    if not isinstance(schema, dict):
        return schema
    result = {}
    for k, v in schema.items():
        if k == '$ref' and isinstance(v, str) and v.startswith('#/'):
            # Resolve JSON pointer within root schema
            parts = v[2:].split('/')
            ref = root
            for p in parts:
                ref = ref.get(p, {})
            return _resolve_refs(ref, root)
        elif k == 'items' and isinstance(v, dict):
            result[k] = _resolve_refs(v, root)
        elif isinstance(v, dict):
            result[k] = _resolve_refs(v, root)
        else:
            result[k] = v
    return result


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python -m lib.json_schema <schema_name> <json_file>")
        print("Schemas: findings, noun_check, suspect_nouns, all_fixes, report")
        sys.exit(1)

    schema_name = sys.argv[1]
    json_path = sys.argv[2]

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    try:
        validate(data, schema_name)
        print(f"✓ {schema_name}: valid")
    except ValidationError as e:
        print(f"✗ {str(e)}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"✗ {e}", file=sys.stderr)
        sys.exit(2)
