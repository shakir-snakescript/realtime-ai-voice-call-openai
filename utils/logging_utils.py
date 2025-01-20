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
        filename = f"call_{timestamp}.log"
        filepath = os.path.join(self.logs_dir, filename)
        
        # Create file handler for this specific call
        file_handler = logging.FileHandler(filepath)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
        self.logger.addHandler(file_handler)
        
        # Log initial call information
        self.log_event("call_started", {
            "call_sid": call_sid,
            "timestamp": datetime.now().isoformat(),
            "status": "initiated"
        })
        
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
            return f"🗣️ User speaking detected (Duration: {details.get('duration', 'unknown')}ms)"
        elif event_type == "ai_response":
            return f"🤖 AI: {details.get('content', '')}"
        elif event_type == "user_speech":
            return f"👤 User: {details.get('content', '')}"
        elif event_type == "call_started":
            return f"📱 Call initiated\n" \
                   f"   CallSID: {details.get('call_sid')}\n" \
                   f"   Timestamp: {details.get('timestamp')}\n" \
                   f"   Initial Status: {details.get('status')}"
        elif event_type == "call_ended":
            return f"🔚 Call ended\n" \
                   f"   Duration: {details.get('duration', 'unknown')}s\n" \
                   f"   Final Status: {details.get('status')}\n" \
                   f"   End Time: {timestamp}"
        elif event_type == "error":
            return f"❌ Error: {details.get('message', 'Unknown error')}\n" \
                   f"   Details: {details.get('details', 'No additional details')}"
        elif event_type == "websocket_connected":
            return f"🌐 WebSocket Connected\n" \
                   f"   Connection ID: {details.get('connection_id', 'unknown')}\n" \
                   f"   Client IP: {details.get('client_ip', 'unknown')}"
        elif event_type == "websocket_disconnected":
            return f"🔌 WebSocket Disconnected\n" \
                   f"   Connection ID: {details.get('connection_id', 'unknown')}\n" \
                   f"   Duration: {details.get('duration', 'unknown')}s"
        elif event_type == "media_received":
            return f"📡 Media Chunk Received\n" \
                   f"   Timestamp: {details.get('media', {}).get('timestamp', 'unknown')}\n" \
                   f"   Size: {len(details.get('media', {}).get('payload', ''))} bytes"
        elif event_type == "speech_started":
            return f"🎤 Speech Started\n" \
                   f"   Timestamp: {details.get('timestamp')}\n" \
                   f"   Energy Level: {details.get('energy_level', 'unknown')}"
        elif event_type == "speech_stopped":
            return f"🛑 Speech Stopped\n" \
                   f"   Duration: {details.get('duration', 'unknown')}ms\n" \
                   f"   Final: {details.get('final', False)}"
        elif event_type == "rate_limit":
            return f"⚠️ Rate Limit Event\n" \
                   f"   Type: {details.get('limit_type', 'unknown')}\n" \
                   f"   Remaining: {details.get('remaining', 'unknown')}\n" \
                   f"   Reset: {details.get('reset_at', 'unknown')}"
        else:
            return f"ℹ️ {event_type}:\n" \
                   f"   Details: {str(details)}"
    
    def format_log_content(self, content: str) -> str:
        """Format log content for HTML display with sections."""
        sections = {
            "Call Summary": [],
            "Call Information": [],
            "Conversation Transcript": [],
            "Connection Events": [],
            "Rate Limits": [],
            "Errors": []
        }
        
        for line in content.split('\n'):
            if '📞' in line or '📱' in line or '🔚' in line:
                if '📱 Call initiated' in line:
                    sections["Call Summary"].append(line)
                else:
                    sections["Call Information"].append(line)
            elif '👤' in line or '🤖' in line:
                sections["Conversation Transcript"].append(line)
            elif '🌐' in line or '🔌' in line:
                sections["Connection Events"].append(line)
            elif '⚠️' in line:
                sections["Rate Limits"].append(line)
            elif '❌' in line:
                sections["Errors"].append(line)
        
        formatted_content = []
        for section, lines in sections.items():
            if lines:
                formatted_content.extend([
                    f"\n=== {section} ===\n",
                    "\n".join(lines),
                    "\n"
                ])
        
        return "\n".join(formatted_content)

    def log_event(self, event_type: str, details: Dict):
        """Log an event both to file and memory."""
        formatted_message = self.format_event(event_type, details)
        self.logger.info(formatted_message)
        
        # Add call status for certain events
        if event_type == "client_disconnected":
            self.log_event("call_ended", {
                "call_sid": details.get('call_sid'),
                "duration": details.get('duration', 'unknown'),
                "status": "completed"
            })
        
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
