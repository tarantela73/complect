"""
ClamAVScanEngine unit tests.

All socket I/O is mocked — no real ClamAV daemon required.
Tests verify:
  - Response parsing:  "OK" → CLEAN, "FOUND" → INFECTED, other → ERROR
  - Timeout           → ERROR  (socket.timeout)
  - Socket failure    → ERROR  (OSError: connection refused, broken pipe, etc.)
  - socket_path taken from settings when not provided explicitly
  - Custom socket_path overrides settings value
  - get_scan_engine() factory returns correct implementation based on settings
"""
from __future__ import annotations

import socket
import uuid
from unittest.mock import MagicMock, patch

from django.test import override_settings

from apps.files.services.clamav_scan_engine import ClamAVScanEngine, get_scan_engine
from apps.files.services.scan_engine import ScanResult, StubScanEngine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_file_record(object_key: str = "tenant/file-id/test.pdf") -> MagicMock:
    record = MagicMock()
    record.id = uuid.uuid4()
    record.object_key = object_key
    return record


def _mock_socket_response(response: str):
    """
    Returns a context manager mock that simulates socket.socket(AF_UNIX, ...).
    recv() returns the encoded response string.
    """
    sock = MagicMock()
    sock.recv.return_value = response.encode()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=sock)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx, sock


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


class TestClamAVResponseParsing:
    @patch("socket.socket")
    def test_ok_response_returns_clean(self, mock_socket_cls):
        ctx, _ = _mock_socket_response("stream: OK")
        mock_socket_cls.return_value = ctx

        engine = ClamAVScanEngine(socket_path="/tmp/test.ctl")
        result = engine.scan(_make_file_record())

        assert result is ScanResult.CLEAN

    @patch("socket.socket")
    def test_found_response_returns_infected(self, mock_socket_cls):
        ctx, _ = _mock_socket_response("stream: Eicar-Test-Signature FOUND")
        mock_socket_cls.return_value = ctx

        engine = ClamAVScanEngine(socket_path="/tmp/test.ctl")
        result = engine.scan(_make_file_record())

        assert result is ScanResult.INFECTED

    @patch("socket.socket")
    def test_unknown_response_returns_error(self, mock_socket_cls):
        ctx, _ = _mock_socket_response("ERROR: Access denied.")
        mock_socket_cls.return_value = ctx

        engine = ClamAVScanEngine(socket_path="/tmp/test.ctl")
        result = engine.scan(_make_file_record())

        assert result is ScanResult.ERROR

    @patch("socket.socket")
    def test_empty_response_returns_error(self, mock_socket_cls):
        ctx, _ = _mock_socket_response("")
        mock_socket_cls.return_value = ctx

        engine = ClamAVScanEngine(socket_path="/tmp/test.ctl")
        result = engine.scan(_make_file_record())

        assert result is ScanResult.ERROR


# ---------------------------------------------------------------------------
# I/O error paths — must never raise, must return ERROR
# ---------------------------------------------------------------------------


class TestClamAVErrorPaths:
    @patch("socket.socket")
    def test_timeout_returns_error(self, mock_socket_cls):
        sock = MagicMock()
        sock.connect.side_effect = socket.timeout("timed out")
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=sock)
        ctx.__exit__ = MagicMock(return_value=False)
        mock_socket_cls.return_value = ctx

        engine = ClamAVScanEngine(socket_path="/tmp/test.ctl")
        result = engine.scan(_make_file_record())

        assert result is ScanResult.ERROR

    @patch("socket.socket")
    def test_connection_refused_returns_error(self, mock_socket_cls):
        sock = MagicMock()
        sock.connect.side_effect = OSError(111, "Connection refused")
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=sock)
        ctx.__exit__ = MagicMock(return_value=False)
        mock_socket_cls.return_value = ctx

        engine = ClamAVScanEngine(socket_path="/tmp/test.ctl")
        result = engine.scan(_make_file_record())

        assert result is ScanResult.ERROR

    @patch("socket.socket")
    def test_recv_timeout_returns_error(self, mock_socket_cls):
        sock = MagicMock()
        sock.recv.side_effect = socket.timeout("recv timed out")
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=sock)
        ctx.__exit__ = MagicMock(return_value=False)
        mock_socket_cls.return_value = ctx

        engine = ClamAVScanEngine(socket_path="/tmp/test.ctl")
        result = engine.scan(_make_file_record())

        assert result is ScanResult.ERROR

    @patch("socket.socket")
    def test_broken_pipe_returns_error(self, mock_socket_cls):
        sock = MagicMock()
        sock.sendall.side_effect = BrokenPipeError()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=sock)
        ctx.__exit__ = MagicMock(return_value=False)
        mock_socket_cls.return_value = ctx

        engine = ClamAVScanEngine(socket_path="/tmp/test.ctl")
        result = engine.scan(_make_file_record())

        assert result is ScanResult.ERROR


# ---------------------------------------------------------------------------
# Configuration: socket_path + timeout
# ---------------------------------------------------------------------------


class TestClamAVConfiguration:
    @override_settings(CLAMAV_SOCKET_PATH="/custom/clamd.ctl")
    def test_socket_path_taken_from_settings(self):
        engine = ClamAVScanEngine()
        assert engine.socket_path == "/custom/clamd.ctl"

    def test_explicit_socket_path_overrides_settings(self):
        engine = ClamAVScanEngine(socket_path="/explicit/path.ctl")
        assert engine.socket_path == "/explicit/path.ctl"

    @override_settings(CLAMAV_SCAN_TIMEOUT_MS=5_000)
    @patch("socket.socket")
    def test_timeout_applied_from_settings(self, mock_socket_cls):
        ctx, sock = _mock_socket_response("stream: OK")
        mock_socket_cls.return_value = ctx

        engine = ClamAVScanEngine(socket_path="/tmp/test.ctl")
        engine.scan(_make_file_record())

        sock.settimeout.assert_called_once_with(5.0)

    @patch("socket.socket")
    def test_object_key_sent_to_daemon(self, mock_socket_cls):
        ctx, sock = _mock_socket_response("stream: OK")
        mock_socket_cls.return_value = ctx

        engine = ClamAVScanEngine(socket_path="/tmp/test.ctl")
        file_record = _make_file_record(object_key="abc/def/invoice.pdf")
        engine.scan(file_record)

        sock.sendall.assert_called_once_with(b"abc/def/invoice.pdf")


# ---------------------------------------------------------------------------
# get_scan_engine() factory
# ---------------------------------------------------------------------------


class TestGetScanEngineFactory:
    @override_settings(FILES_SCAN_ENGINE="STUB")
    def test_stub_setting_returns_stub_engine(self):
        engine = get_scan_engine()
        assert isinstance(engine, StubScanEngine)

    @override_settings(FILES_SCAN_ENGINE="CLAMAV", CLAMAV_SOCKET_PATH="/tmp/clamd.ctl")
    def test_clamav_setting_returns_clamav_engine(self):
        engine = get_scan_engine()
        assert isinstance(engine, ClamAVScanEngine)

    @override_settings(FILES_SCAN_ENGINE="CLAMAV", CLAMAV_SOCKET_PATH="/run/clamav/clamd.ctl")
    def test_clamav_engine_has_correct_socket_path(self):
        engine = get_scan_engine()
        assert isinstance(engine, ClamAVScanEngine)
        assert engine.socket_path == "/run/clamav/clamd.ctl"
