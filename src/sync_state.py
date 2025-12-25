"""Sync state management for tracking last sync timestamps."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class SyncState:
    """Manages sync state to track what data has been synchronized."""

    def __init__(self, state_file: Path):
        """Initialize sync state.
        
        Args:
            state_file: Path to state file
        """
        self.state_file = state_file
        self.last_sync: Optional[datetime] = None
        self.last_successful_date: Optional[str] = None
        
        self._load_state()
    
    def _load_state(self) -> None:
        """Load state from file."""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                
                if data.get('last_sync'):
                    self.last_sync = datetime.fromisoformat(data['last_sync'])
                
                self.last_successful_date = data.get('last_successful_date')
                
                logger.info(f"Loaded sync state: last_sync={self.last_sync}, "
                          f"last_successful_date={self.last_successful_date}")
            except Exception as e:
                logger.error(f"Error loading sync state: {e}")
    
    def _save_state(self) -> None:
        """Save state to file."""
        data = {
            'last_sync': self.last_sync.isoformat() if self.last_sync else None,
            'last_successful_date': self.last_successful_date,
        }
        
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(self.state_file, 'w') as f:
                json.dump(data, f, indent=2)
            
            logger.debug("Saved sync state")
        except Exception as e:
            logger.error(f"Error saving sync state: {e}")
    
    def update_last_sync(self, date_str: str) -> None:
        """Update last successful sync.
        
        Args:
            date_str: Date string in YYYY-MM-DD format
        """
        self.last_sync = datetime.now()
        self.last_successful_date = date_str
        self._save_state()
        logger.info(f"Updated sync state: {date_str}")
    
    def get_last_successful_date(self) -> Optional[str]:
        """Get last successfully synced date.
        
        Returns:
            Date string or None
        """
        return self.last_successful_date
