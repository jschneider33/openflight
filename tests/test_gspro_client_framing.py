"""Tests for the TCP framing helper in GSProClient.

OpenConnectV1 has no length prefix or delimiter — frames are detected by
balanced top-level braces. These tests exercise the static brace-counter so
we don't have to spin up the mock server for every framing edge case.
"""
from openflight.gspro.client import GSProClient


def test_complete_object_returns_end_index():
    assert GSProClient._find_json_end(b'{"a":1}') == 7


def test_partial_object_returns_none():
    assert GSProClient._find_json_end(b'{"a":') is None
    assert GSProClient._find_json_end(b'{"a":1') is None


def test_two_concatenated_objects_returns_first_end():
    # Returns the index past the first object only; caller drains in a loop.
    raw = b'{"a":1}{"b":2}'
    end = GSProClient._find_json_end(raw)
    assert end == 7
    assert raw[end:] == b'{"b":2}'


def test_braces_inside_strings_dont_count():
    raw = b'{"msg":"hi {there} }"}'
    end = GSProClient._find_json_end(raw)
    assert end == len(raw)


def test_escaped_quote_inside_string():
    raw = b'{"msg":"she said \\"hi\\" }"}'
    end = GSProClient._find_json_end(raw)
    assert end == len(raw)


def test_nested_objects():
    raw = b'{"outer":{"inner":1}}'
    end = GSProClient._find_json_end(raw)
    assert end == len(raw)


def test_empty_buffer_returns_none():
    assert GSProClient._find_json_end(b'') is None


def test_leading_whitespace_before_object():
    raw = b'  \n{"a":1}'
    end = GSProClient._find_json_end(raw)
    assert end == len(raw)


def test_non_ascii_inside_string():
    raw = b'{"msg":"caf\xc3\xa9"}'
    end = GSProClient._find_json_end(raw)
    assert end == len(raw)
