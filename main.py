import os
import json
import base64
import asyncio
import websockets
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from dotenv import load_dotenv
import pandas as pd
import logging
from datetime import datetime
from typing import List, Dict
from utils.logging_utils import CallLogger
from pathlib import Path
import logging
import html

logger = logging.getLogger(__name__)
load_dotenv()

# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
PORT = int(os.getenv('PORT', 5050))

# Add knowledge base loading function
def load_knowledge_base(csv_path):
    """Load and format knowledge base from CSV file."""
    try:
        df = pd.read_csv(csv_path)
        # Assuming your CSV has 'question' and 'answer' columns
        knowledge_base = "\n\n".join([
            f"Q: {row['question']}\nA: {row['answer']}"
            for _, row in df.iterrows()
        ])
        return knowledge_base
    except Exception as e:
        print(f"Error loading knowledge base: {e}")
        return ""

# SYSTEM_MESSAGE = """
#     You are a helpful and knowledgeable assistant. Your role is to provide clear, accurate, 
#     and friendly responses to any questions or topics the user brings up.

#     Here is your knowledge base of questions and answers:

#     {knowledge_base}

#     When answering questions, prioritize information from your knowledge base if relevant. 
#     If a question isn't covered in the knowledge base, you can draw from your general knowledge.
# """

# SAGE (Snakescript's Advanced Guidance Expert)
SYSTEM_MESSAGE = """
    I am SAGE, your engaging voice assistant for this conversation. My responses will be:
    - Brief and clear (aim for 2-3 sentences when possible)
    - Natural and conversational, not robotic
    - Easy to understand over the phone

    Below is my specialized knowledge base:
    {knowledge_base}

    Guidelines for responses:
    1. When a question matches my knowledge base:
       - Understand the core information from the relevant answer
       - Rephrase it naturally in my own words
       - Add brief context if needed for clarity
    
    2. When a question partially matches:
       - Combine relevant knowledge base information with my general knowledge
       - Prioritize the knowledge base information but present it conversationally
    
    3. For unrelated questions:
       - Draw from my general knowledge
       - Keep the same conversational, concise style

    Remember:
    - If asked about my name, explain that SAGE stands for Snakescript's Advanced Guidance Expert
    - Speak as if having a friendly phone conversation
    - Avoid technical jargon unless specifically asked
    - If you need to list items, limit to 3 key points
    - Use natural transitions and acknowledgments (e.g., "I understand...", "Great question...", "Ah, I see...", "Uh-huh", "Mm-hmm")
"""

VOICE = 'alloy'
LOG_EVENT_TYPES = [
    'error', 'response.content.done', 'rate_limits.updated',
    'response.done', 'input_audio_buffer.committed',
    'input_audio_buffer.speech_stopped', 'input_audio_buffer.speech_started',
    'session.created'
]
SHOW_TIMING_MATH = False

app = FastAPI()

if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file.')

# Replace the existing logging setup with:
call_logger = CallLogger()
call_logs: List[Dict] = []

@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio Media Stream Server is running!"}

@app.get("/logs", response_class=HTMLResponse)
async def get_logs():
    """Return list of available log files in HTML format."""
    logs_dir = Path("logs")
    
    # Get all log files from the logs directory
    log_files = []
    for file in logs_dir.glob("*.log"):
        try:
            # Get file metadata
            log_files.append({
                "filename": file.name,
                "call_sid": file.name.split('_')[1].replace('.log', ''),
                "last_modified": datetime.fromtimestamp(os.path.getmtime(file)).strftime("%Y-%m-%d %H:%M:%S"),
                "size": f"{os.path.getsize(file) / 1024:.1f} KB"
            })
        except Exception as e:
            logger.error(f"Error processing log file {file}: {str(e)}")
    
    # Sort files by last modified date (newest first)
    log_files = sorted(log_files, key=lambda x: x["last_modified"], reverse=True)
    
    # Create HTML content with double curly braces for CSS
    html_content = """
    <html>
        <head>
            <title>Call Logs</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                h1 {{ color: #333; }}
                table {{ border-collapse: collapse; width: 100%; }}
                th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }}
                th {{ background-color: #f5f5f5; }}
                tr:hover {{ background-color: #f9f9f9; }}
                a {{ color: #0066cc; text-decoration: none; }}
                a:hover {{ text-decoration: underline; }}
                .total {{ margin-bottom: 20px; color: #666; }}
            </style>
        </head>
        <body>
            <h1>Call Logs</h1>
            <div class="total">Total Files: {total_files}</div>
            <table>
                <tr>
                    <th>Filename</th>
                    <th>Call SID</th>
                    <th>Last Modified</th>
                    <th>Size</th>
                </tr>
                {table_rows}
            </table>
        </body>
    </html>
    """
    
    # Generate table rows
    table_rows = ""
    for file in log_files:
        table_rows += f"""
            <tr>
                <td><a href="/logs/{file['filename']}">{file['filename']}</a></td>
                <td>{file['call_sid']}</td>
                <td>{file['last_modified']}</td>
                <td>{file['size']}</td>
            </tr>
        """
    
    return html_content.format(
        total_files=len(log_files),
        table_rows=table_rows
    )

@app.get("/logs/{filename}", response_class=HTMLResponse)
async def get_log_content(filename: str):
    """Return content of a specific log file in HTML format."""
    log_file = Path("logs") / filename
    
    try:
        if not log_file.exists():
            return HTMLResponse(
                content="""
                <html>
                    <body>
                        <h1>Error</h1>
                        <p>Log file not found</p>
                        <p><a href="/logs">Back to logs</a></p>
                    </body>
                </html>
                """,
                status_code=404
            )
            
        with open(log_file, 'r') as f:
            content = f.read()
        
        # Format the content with sections
        formatted_content = call_logger.format_log_content(content)
        last_modified = datetime.fromtimestamp(os.path.getmtime(log_file)).strftime("%Y-%m-%d %H:%M:%S")
        
        return f"""
        <html>
            <head>
                <title>Log File: {filename}</title>
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 20px; }}
                    h1 {{ color: #333; }}
                    h2 {{ color: #666; margin-top: 30px; }}
                    .metadata {{ color: #666; margin-bottom: 20px; }}
                    .content {{ 
                        background-color: #f5f5f5;
                        padding: 20px;
                        border-radius: 5px;
                        white-space: pre-wrap;
                        font-family: monospace;
                    }}
                    .transcript {{ 
                        background-color: #fff;
                        border: 1px solid #ddd;
                        margin: 10px 0;
                        padding: 15px;
                    }}
                    .user-message {{ color: #2c5282; }}
                    .ai-message {{ color: #2b6cb0; }}
                    .error {{ color: #c53030; }}
                    .back-link {{ margin-top: 20px; }}
                    a {{ color: #0066cc; text-decoration: none; }}
                    a:hover {{ text-decoration: underline; }}
                </style>
            </head>
            <body>
                <h1>Log File: {filename}</h1>
                <div class="metadata">Last Modified: {last_modified}</div>
                <div class="content">{formatted_content}</div>
                <div class="back-link">
                    <a href="/logs">← Back to logs</a>
                </div>
            </body>
        </html>
        """
        
    except Exception as e:
        return HTMLResponse(
            content=f"""
            <html>
                <body>
                    <h1>Error</h1>
                    <p>Error reading log file: {str(e)}</p>
                    <p><a href="/logs">Back to logs</a></p>
                </body>
            </html>
            """,
            status_code=500
        )

@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """Handle incoming call and return TwiML response to connect to Media Stream."""
    call_sid = request.query_params.get('CallSid', 'unknown')
    log_entry = call_logger.log_event("incoming_call", {"call_sid": call_sid})
    call_logs.append(log_entry)
    
    # Create a new log file for this call
    call_logger.create_call_log(call_sid)
    
    response = VoiceResponse()
    response.say("Please wait while we connect your call to the AI voice assistant.")
    host = request.url.hostname
    connect = Connect()
    connect.stream(url=f'wss://{host}/media-stream')
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")

@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """Handle WebSocket connections between Twilio and OpenAI."""
    log_entry = call_logger.log_event("websocket_connected", {})
    call_logs.append(log_entry)
    
    logger.info("New WebSocket connection established")
    await websocket.accept()

    async with websockets.connect(
        'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01',
        extra_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1"
        }
    ) as openai_ws:
        await initialize_session(openai_ws)

        # Connection specific state
        stream_sid = None
        latest_media_timestamp = 0
        last_assistant_item = None
        mark_queue = []
        response_start_timestamp_twilio = None
        
        async def receive_from_twilio():
            """Receive audio data from Twilio and send it to the OpenAI Realtime API."""
            nonlocal stream_sid, latest_media_timestamp
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data['event'] == 'media' and openai_ws.open:
                        latest_media_timestamp = int(data['media']['timestamp'])
                        audio_append = {
                            "type": "input_audio_buffer.append",
                            "audio": data['media']['payload']
                        }
                        await openai_ws.send(json.dumps(audio_append))
                    elif data['event'] == 'start':
                        stream_sid = data['start']['streamSid']
                        log_entry = call_logger.log_event("stream_started", {"stream_sid": stream_sid})
                        call_logs.append(log_entry)
                        logger.info(f"Stream started - StreamSid: {stream_sid}")
                        call_logs.append({
                            "timestamp": datetime.now().isoformat(),
                            "event": "stream_started",
                            "stream_sid": stream_sid
                        })
                        response_start_timestamp_twilio = None
                        latest_media_timestamp = 0
                        last_assistant_item = None
                    elif data['event'] == 'mark':
                        if mark_queue:
                            mark_queue.pop(0)
            except WebSocketDisconnect:
                log_entry = call_logger.log_event("client_disconnected", {"stream_sid": stream_sid})
                call_logs.append(log_entry)
                logger.info(f"Client disconnected - StreamSid: {stream_sid}")
                call_logs.append({
                    "timestamp": datetime.now().isoformat(),
                    "event": "client_disconnected",
                    "stream_sid": stream_sid
                })
                if openai_ws.open:
                    await openai_ws.close()

        async def send_to_twilio():
            """Receive events from the OpenAI Realtime API, send audio back to Twilio."""
            nonlocal stream_sid, last_assistant_item, response_start_timestamp_twilio
            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)
                    
                    # Log all important events
                    if response['type'] in LOG_EVENT_TYPES:
                        print(f"Received event: {response['type']}", response)
                    
                    # Log transcripts and AI responses
                    if response.get('type') == 'response.content.part':
                        log_entry = call_logger.log_event(
                            "ai_response",
                            {
                                "content": response.get('content', ''),
                                "stream_sid": stream_sid,
                                "timestamp": datetime.now().isoformat()
                            }
                        )
                        call_logs.append(log_entry)
                    
                    elif response.get('type') == 'input_audio_buffer.speech_stopped':
                        if response.get('text'):
                            log_entry = call_logger.log_event(
                                "user_speech",
                                {
                                    "content": response.get('text', ''),
                                    "stream_sid": stream_sid,
                                    "timestamp": datetime.now().isoformat()
                                }
                            )
                            call_logs.append(log_entry)

                    if response.get('type') == 'response.audio.delta' and 'delta' in response:
                        audio_payload = base64.b64encode(base64.b64decode(response['delta'])).decode('utf-8')
                        audio_delta = {
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {
                                "payload": audio_payload
                            }
                        }
                        await websocket.send_json(audio_delta)

                        if response_start_timestamp_twilio is None:
                            response_start_timestamp_twilio = latest_media_timestamp
                            if SHOW_TIMING_MATH:
                                print(f"Setting start timestamp for new response: {response_start_timestamp_twilio}ms")

                        # Update last_assistant_item safely
                        if response.get('item_id'):
                            last_assistant_item = response['item_id']

                        await send_mark(websocket, stream_sid)

                    # Trigger an interruption. Your use case might work better using `input_audio_buffer.speech_stopped`, or combining the two.
                    if response.get('type') == 'input_audio_buffer.speech_started':
                        print("Speech started detected.")
                        if last_assistant_item:
                            print(f"Interrupting response with id: {last_assistant_item}")
                            await handle_speech_started_event()
            except Exception as e:
                print(f"Error in send_to_twilio: {e}")

        async def handle_speech_started_event():
            """Handle interruption when the caller's speech starts."""
            nonlocal response_start_timestamp_twilio, last_assistant_item
            print("Handling speech started event.")
            if mark_queue and response_start_timestamp_twilio is not None:
                elapsed_time = latest_media_timestamp - response_start_timestamp_twilio
                if SHOW_TIMING_MATH:
                    print(f"Calculating elapsed time for truncation: {latest_media_timestamp} - {response_start_timestamp_twilio} = {elapsed_time}ms")

                if last_assistant_item:
                    if SHOW_TIMING_MATH:
                        print(f"Truncating item with ID: {last_assistant_item}, Truncated at: {elapsed_time}ms")

                    truncate_event = {
                        "type": "conversation.item.truncate",
                        "item_id": last_assistant_item,
                        "content_index": 0,
                        "audio_end_ms": elapsed_time
                    }
                    await openai_ws.send(json.dumps(truncate_event))

                await websocket.send_json({
                    "event": "clear",
                    "streamSid": stream_sid
                })

                mark_queue.clear()
                last_assistant_item = None
                response_start_timestamp_twilio = None

        async def send_mark(connection, stream_sid):
            if stream_sid:
                mark_event = {
                    "event": "mark",
                    "streamSid": stream_sid,
                    "mark": {"name": "responsePart"}
                }
                await connection.send_json(mark_event)
                mark_queue.append('responsePart')

        await asyncio.gather(receive_from_twilio(), send_to_twilio())

async def send_initial_conversation_item(openai_ws):
    """Send initial conversation item if AI talks first."""
    initial_conversation_item = {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "Hello there! How can I help you today?"
                }
            ]
        }
    }
    await openai_ws.send(json.dumps(initial_conversation_item))
    await openai_ws.send(json.dumps({"type": "response.create"}))


async def initialize_session(openai_ws):
    """Control initial session with OpenAI."""
    logger.info("Initializing OpenAI session")
    # Load knowledge base
    knowledge_base = load_knowledge_base("snakescript_kb.csv")
    
    session_update = {
        "type": "session.update",
        "session": {
            "turn_detection": {"type": "server_vad"},
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": VOICE,
            "instructions": SYSTEM_MESSAGE.format(knowledge_base=knowledge_base),
            "modalities": ["text", "audio"],
            "temperature": 0.8,
        }
    }
    logger.debug('Sending session update: %s', json.dumps(session_update))
    await openai_ws.send(json.dumps(session_update))

    # Uncomment the next line to have the AI speak first
    # await send_initial_conversation_item(openai_ws)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
