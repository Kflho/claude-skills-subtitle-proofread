"""Tests for lib/json_schema.py — schema validation of data contracts."""

import json
import os
import pytest
from lib.json_schema import validate, ValidationError, _validate_type, _validate_object


# ═══════════════════════════════════════════════════════════════
# Unit tests: type validation
# ═══════════════════════════════════════════════════════════════

class TestValidateType:
    def test_string_valid(self):
        assert _validate_type("hello", "string") == []

    def test_string_invalid(self):
        assert len(_validate_type(123, "string")) == 1

    def test_integer_valid(self):
        assert _validate_type(42, "integer") == []

    def test_integer_rejects_bool(self):
        """bool is a subclass of int, but schema 'integer' should reject it."""
        assert len(_validate_type(True, "integer")) == 1

    def test_number_valid_int(self):
        assert _validate_type(42, "number") == []

    def test_number_valid_float(self):
        assert _validate_type(3.14, "number") == []

    def test_number_invalid(self):
        assert len(_validate_type("42", "number")) == 1

    def test_boolean_valid(self):
        assert _validate_type(True, "boolean") == []
        assert _validate_type(False, "boolean") == []

    def test_array_valid(self):
        assert _validate_type([1, 2], "array") == []

    def test_object_valid(self):
        assert _validate_type({"a": 1}, "object") == []


# ═══════════════════════════════════════════════════════════════
# Unit tests: object validation
# ═══════════════════════════════════════════════════════════════

class TestValidateObject:
    def test_valid(self):
        schema = {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}}
        }
        assert _validate_object({"name": "test"}, schema) == []

    def test_missing_required(self):
        schema = {"type": "object", "required": ["name"], "properties": {}}
        errors = _validate_object({}, schema)
        assert len(errors) == 1
        assert "missing required" in errors[0].lower()

    def test_wrong_type(self):
        schema = {
            "type": "object",
            "properties": {"age": {"type": "integer"}}
        }
        errors = _validate_object({"age": "old"}, schema)
        assert len(errors) == 1

    def test_nested_object(self):
        schema = {
            "type": "object",
            "properties": {
                "meta": {
                    "type": "object",
                    "required": ["version"],
                    "properties": {"version": {"type": "integer"}}
                }
            }
        }
        assert _validate_object({"meta": {"version": 1}}, schema) == []

    def test_array_items(self):
        schema = {
            "type": "object",
            "properties": {
                "entries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["ep"],
                        "properties": {"ep": {"type": "string"}}
                    }
                }
            }
        }
        assert _validate_object(
            {"entries": [{"ep": "EP001"}, {"ep": "EP002"}]},
            schema
        ) == []

    def test_array_item_invalid(self):
        schema = {
            "type": "object",
            "properties": {
                "entries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["ep"],
                        "properties": {"ep": {"type": "string"}}
                    }
                }
            }
        }
        errors = _validate_object(
            {"entries": [{"ep": 123}]},
            schema
        )
        assert len(errors) == 1


# ═══════════════════════════════════════════════════════════════
# Integration: validate against schema files
# ═══════════════════════════════════════════════════════════════

class TestValidateReport:
    def test_valid_minimal(self):
        data = {
            "version": 1,
            "updated": "2026-01-01T00:00:00",
            "layers": {
                "2": [
                    {"ep": "EP001", "time": "00:00:01.000",
                     "original": "bad", "corrected": "good", "status": "✅"}
                ]
            }
        }
        validate(data, "report")  # should not raise

    def test_invalid_status(self):
        data = {
            "version": 1,
            "layers": {
                "2": [
                    {"ep": "EP001", "time": "00:00:01.000",
                     "original": "bad", "corrected": "", "status": "INVALID"}
                ]
            }
        }
        with pytest.raises(ValidationError):
            validate(data, "report")

    def test_missing_version(self):
        data = {"layers": {}}
        with pytest.raises(ValidationError):
            validate(data, "report")


class TestValidateFindings:
    def test_valid_minimal(self):
        data = {
            "garbled_cues": [],
            "missing_subtitles": {},
            "summary": {
                "files_scanned": 1,
                "total_cues": 100,
                "garbled_count": 0,
                "episodes_with_issues": 0
            }
        }
        validate(data, "findings")  # should not raise


class TestValidateAllFixes:
    def test_valid(self):
        data = [
            {"action": "replace_text", "file": "test.srt",
             "start": "00:00:01.000", "original": "bad", "replacement": "good"}
        ]
        validate(data, "all_fixes")

    def test_invalid_action(self):
        data = [{"action": "unknown_action", "file": "test.srt"}]
        with pytest.raises(ValidationError):
            validate(data, "all_fixes")
