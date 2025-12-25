"""Tests for sync state management."""

import json
import pytest
from datetime import datetime

from src.sync_state import SyncState


def test_sync_state_creation(temp_state_file):
    """Test creating new sync state file."""
    state = SyncState(temp_state_file)
    
    assert state.get_last_successful_date() is None
    assert not temp_state_file.exists()


def test_sync_state_update(temp_state_file):
    """Test updating sync state."""
    state = SyncState(temp_state_file)
    
    state.update_last_sync("2025-12-24")
    
    assert state.get_last_successful_date() == "2025-12-24"
    assert temp_state_file.exists()


def test_sync_state_persistence(temp_state_file):
    """Test sync state persists across instances."""
    # Create and update state
    state1 = SyncState(temp_state_file)
    state1.update_last_sync("2025-12-24")
    
    # Create new instance and verify it loads saved state
    state2 = SyncState(temp_state_file)
    assert state2.get_last_successful_date() == "2025-12-24"


def test_sync_state_file_format(temp_state_file):
    """Test sync state file has correct JSON format."""
    state = SyncState(temp_state_file)
    state.update_last_sync("2025-12-24")
    
    # Read file directly and verify structure
    with open(temp_state_file, 'r') as f:
        data = json.load(f)
    
    assert "last_sync" in data
    assert "last_successful_date" in data
    assert data["last_successful_date"] == "2025-12-24"


def test_sync_state_invalid_json(temp_state_file):
    """Test handling of corrupted state file."""
    # Write invalid JSON
    with open(temp_state_file, 'w') as f:
        f.write("invalid json {")
    
    # Should handle gracefully and start fresh
    state = SyncState(temp_state_file)
    assert state.get_last_successful_date() is None
