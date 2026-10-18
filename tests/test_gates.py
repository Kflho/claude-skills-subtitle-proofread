"""Tests for lib/gates.py — behaviour tiers and L1 unlock switches."""

import argparse

import pytest

from lib import gates


def test_get_unknown_key_raises():
    with pytest.raises(KeyError):
        gates.get('nope')


def test_gate_keys_are_unique_and_levels_valid():
    keys = [g.key for g in gates.GATES]
    assert len(keys) == len(set(keys))
    assert {g.level for g in gates.GATES} <= {gates.L0, gates.L1,
                                              gates.L2, gates.L3}
    for gate in gates.GATES:
        assert gate.what
        if gate.level == gates.L0:
            assert gate.unlock == '—'
        if gate.level == gates.L1:
            assert gate.unlock.startswith('--')


def test_level_of_known_keys():
    assert gates.level_of('scan') == gates.L0
    assert gates.level_of('vad_delete') == gates.L1
    assert gates.level_of('overwrite_deliverable') == gates.L3


def test_l1_unlock_requires_explicit_flag():
    assert gates.enabled('vad_delete', argparse.Namespace()) is False
    assert gates.enabled('vad_delete',
                         argparse.Namespace(vad_clean_apply=False)) is False
    assert gates.enabled('vad_delete',
                         argparse.Namespace(vad_clean_apply=True)) is True


def test_non_l1_gates_are_not_decided_here():
    assert gates.enabled('scan') is True
    assert gates.enabled('polish', argparse.Namespace(polish=False)) is True


def test_format_table_lists_every_gate():
    table = gates.format_table()
    for gate in gates.GATES:
        assert gate.key in table
    assert 'L0' in table and 'L1' in table
    assert '--vad-clean-apply' in table
