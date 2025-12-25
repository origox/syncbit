"""Tests for main entry point."""

import logging
import sys
from unittest.mock import MagicMock, patch

import pytest

import main
from src.config import Config


def test_setup_logging_stdout_only(monkeypatch, tmp_path):
    """Test setup_logging with only stdout handler when data dir doesn't exist."""
    # Use non-existent data dir
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path / "nonexistent")

    # Clear any existing handlers
    logging.root.handlers = []

    main.setup_logging("DEBUG")

    # Should have at least a StreamHandler
    stream_handlers = [h for h in logging.root.handlers if isinstance(h, logging.StreamHandler)]
    assert len(stream_handlers) >= 1
    # Should NOT have a FileHandler since data dir doesn't exist
    file_handlers = [h for h in logging.root.handlers if isinstance(h, logging.FileHandler)]
    assert len(file_handlers) == 0


def test_setup_logging_with_file_handler(monkeypatch, tmp_path):
    """Test setup_logging creates file handler when data dir exists."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(Config, "DATA_DIR", data_dir)

    # Clear any existing handlers
    logging.root.handlers = []

    main.setup_logging("INFO")

    # Check that log file was created
    log_file = data_dir / "syncbit.log"
    file_handlers = [h for h in logging.root.handlers if isinstance(h, logging.FileHandler)]
    assert len(file_handlers) >= 1
    # Verify the log file exists
    assert log_file.exists()


def test_setup_logging_levels(tmp_path):
    """Test setup_logging accepts different log levels."""
    # Test with non-existent dir to avoid file handler issues
    non_existent = tmp_path / "nonexistent"

    for level in ["DEBUG", "INFO", "WARNING", "ERROR"]:
        # Clear handlers before each test
        logging.root.handlers = []

        # Temporarily set DATA_DIR
        original_data_dir = Config.DATA_DIR
        Config.DATA_DIR = non_existent

        main.setup_logging(level)

        # Test that we can log at this level
        test_logger = logging.getLogger(f"test_{level}")
        test_logger.log(getattr(logging, level), f"Test {level}")

        # Restore
        Config.DATA_DIR = original_data_dir


@patch("main.FitbitAuth")
def test_authorize_already_authorized_decline(mock_auth_class, monkeypatch):
    """Test authorize when already authorized and user declines re-auth."""
    mock_auth = MagicMock()
    mock_auth.is_authorized.return_value = True
    mock_auth_class.return_value = mock_auth

    # Mock input to decline re-authorization
    monkeypatch.setattr("builtins.input", lambda _: "n")

    main.authorize()

    # Should check authorization but not call authorize again
    mock_auth.is_authorized.assert_called_once()
    mock_auth.authorize.assert_not_called()


@patch("main.FitbitAuth")
def test_authorize_already_authorized_accept(mock_auth_class, monkeypatch):
    """Test authorize when already authorized and user accepts re-auth."""
    mock_auth = MagicMock()
    mock_auth.is_authorized.return_value = True
    mock_auth_class.return_value = mock_auth

    # Mock input to accept re-authorization
    monkeypatch.setattr("builtins.input", lambda _: "y")

    main.authorize()

    # Should check authorization and call authorize
    mock_auth.is_authorized.assert_called_once()
    mock_auth.authorize.assert_called_once()


@patch("main.FitbitAuth")
def test_authorize_not_authorized(mock_auth_class):
    """Test authorize when not yet authorized."""
    mock_auth = MagicMock()
    mock_auth.is_authorized.return_value = False
    mock_auth_class.return_value = mock_auth

    main.authorize()

    # Should call authorize without prompting
    mock_auth.is_authorized.assert_called_once()
    mock_auth.authorize.assert_called_once()


@patch("main.FitbitAuth")
def test_authorize_failure(mock_auth_class):
    """Test authorize handles authorization failure."""
    mock_auth = MagicMock()
    mock_auth.is_authorized.return_value = False
    mock_auth.authorize.side_effect = Exception("Auth failed")
    mock_auth_class.return_value = mock_auth

    with pytest.raises(SystemExit) as exc_info:
        main.authorize()

    assert exc_info.value.code == 1


@patch("main.SyncScheduler")
def test_run_sync(mock_scheduler_class):
    """Test run_sync starts the scheduler."""
    mock_scheduler = MagicMock()
    mock_scheduler_class.return_value = mock_scheduler

    main.run_sync()

    mock_scheduler_class.assert_called_once()
    mock_scheduler.start.assert_called_once()


@patch("main.run_sync")
@patch("main.setup_logging")
@patch.object(Config, "validate")
def test_main_default_run_sync(mock_validate, mock_setup_logging, mock_run_sync):
    """Test main runs sync by default."""
    test_args = ["main.py"]
    with patch.object(sys, "argv", test_args):
        main.main()

    mock_validate.assert_called_once()
    mock_setup_logging.assert_called_once_with("INFO")
    mock_run_sync.assert_called_once()


@patch("main.authorize")
@patch("main.setup_logging")
@patch.object(Config, "validate")
def test_main_authorize_flag(mock_validate, mock_setup_logging, mock_authorize):
    """Test main runs authorize when --authorize flag is set."""
    test_args = ["main.py", "--authorize"]
    with patch.object(sys, "argv", test_args):
        main.main()

    mock_validate.assert_called_once()
    mock_setup_logging.assert_called_once_with("INFO")
    mock_authorize.assert_called_once()


@patch("main.run_sync")
@patch("main.setup_logging")
@patch.object(Config, "validate")
def test_main_custom_log_level(mock_validate, mock_setup_logging, mock_run_sync):
    """Test main accepts custom log level."""
    test_args = ["main.py", "--log-level", "DEBUG"]
    with patch.object(sys, "argv", test_args):
        main.main()

    mock_validate.assert_called_once()
    mock_setup_logging.assert_called_once_with("DEBUG")
    mock_run_sync.assert_called_once()


@patch("main.run_sync")
@patch("main.setup_logging")
@patch.object(Config, "validate")
def test_main_keyboard_interrupt(mock_validate, mock_setup_logging, mock_run_sync):
    """Test main handles KeyboardInterrupt gracefully."""
    mock_run_sync.side_effect = KeyboardInterrupt()

    test_args = ["main.py"]
    with patch.object(sys, "argv", test_args), pytest.raises(SystemExit) as exc_info:
        main.main()

    assert exc_info.value.code == 0


@patch("main.run_sync")
@patch("main.setup_logging")
@patch.object(Config, "validate")
def test_main_exception_handling(mock_validate, mock_setup_logging, mock_run_sync):
    """Test main handles exceptions and exits with error code."""
    mock_run_sync.side_effect = Exception("Something went wrong")

    test_args = ["main.py"]
    with patch.object(sys, "argv", test_args), pytest.raises(SystemExit) as exc_info:
        main.main()

    assert exc_info.value.code == 1


@patch("main.run_sync")
@patch("main.setup_logging")
@patch.object(Config, "validate")
def test_main_validation_error(mock_validate, mock_setup_logging, mock_run_sync):
    """Test main handles Config validation errors."""
    mock_validate.side_effect = ValueError("Invalid config")

    test_args = ["main.py"]
    with patch.object(sys, "argv", test_args), pytest.raises(SystemExit) as exc_info:
        main.main()

    assert exc_info.value.code == 1
    mock_setup_logging.assert_not_called()
    mock_run_sync.assert_not_called()
