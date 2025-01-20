import os
import logging
from datetime import datetime
from typing import Dict
from pathlib import Path

class CallLogger:
    def __init__(self):
        # Ensure logs directory exists
        self.logs_dir = "logs"
        os.makedirs(self.logs_dir, exist_ok=True)
        
        # Configure main logger
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        
        # Add console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        self.logger.addHandler(console_handler)
        
    def create_call_log(self, call_sid: str) -> str:
        """Create a new log file for a call and return the file path."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}.log"
        filepath = os.path.join(self.logs_dir, filename)
        
        # Create file handler for this specific call
        file_handler = logging.FileHandler(filepath)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
        self.logger.addHandler(file_handler)
        
        return filepath
    
    def format_event(self, event_type: str, details: Dict) -> str:
        """Format event details in a human-readable way."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        if event_type == "incoming_call":
            return f"📞 New call received (CallSID: {details.get('call_sid')})"
        elif event_type == "stream_started":
            return f"🔌 Stream connected (StreamSID: {details.get('stream_sid')})"
        elif event_type == "client_disconnected":
            return f"👋 Call ended (StreamSID: {details.get('stream_sid')})"
        elif event_type == "speech_detected":
            return f"🗣️ User speaking detected"
        elif event_type == "ai_response":
            return f"🤖 AI responding: {details.get('content', '')}"
        else:
            return f"ℹ️ {event_type}: {str(details)}"
    
    def log_event(self, event_type: str, details: Dict):
        """Log an event both to file and memory."""
        formatted_message = self.format_event(event_type, details)
        self.logger.info(formatted_message)
        
        return {
            "timestamp": datetime.now().isoformat(),
            "event": event_type,
            "formatted_message": formatted_message,
            **details
        }

    def read_call_log(self, call_sid: str) -> str:
        """Read the log file for a specific call."""
        # Update the file pattern to match the actual filename format
        timestamp_pattern = datetime.now().strftime("%Y%m%d")
        log_dir = Path(self.logs_dir)
        
        # Look for files matching the pattern
        matching_files = list(log_dir.glob(f"*_{call_sid}.log"))
        
        if not matching_files:
            raise FileNotFoundError(f"No log file found for call_sid: {call_sid}")
        
        # Use the most recent file if multiple exist
        log_file = sorted(matching_files)[-1]
        
        with open(log_file, 'r') as f:
            return f.read()
