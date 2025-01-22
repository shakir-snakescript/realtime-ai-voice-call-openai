import os
import json
import base64
import asyncio
import websockets
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect
import pandas as pd
from dotenv import load_dotenv
import logging
from datetime import datetime
import glob

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

# SAGE (Snakescript's Advanced Guidance Expert)
SYSTEM_MESSAGE = (
    """
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
)

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

@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio Media Stream Server is running!"}

@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """Handle incoming call and return TwiML response to connect to Media Stream."""
    response = VoiceResponse()
    host = request.url.hostname
    connect = Connect()
    connect.stream(url=f'wss://{host}/media-stream')
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")

@app.get("/logs", response_class=HTMLResponse)
async def get_logs():
    """Return an HTML table of available log files."""
    log_files = glob.glob('calls/*.log')
    files_info = []
    
    for file_path in log_files:
        stats = os.stat(file_path)
        files_info.append({
            'filename': os.path.basename(file_path),
            'modified': datetime.fromtimestamp(stats.st_mtime),
            'size': stats.st_size
        })
    
    # Sort files by modified date (newest first)
    files_info.sort(key=lambda x: x['modified'], reverse=True)
    
    # Convert size to human-readable format
    def format_size(size):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Call Logs</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 20px;
            }}
            h1 {{
                color: #333;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                margin-top: 20px;
            }}
            th, td {{
                padding: 12px;
                text-align: left;
                border-bottom: 1px solid #ddd;
            }}
            th {{
                background-color: #f5f5f5;
                font-weight: bold;
            }}
            tr:hover {{
                background-color: #f9f9f9;
            }}
            .file-link {{
                color: #0066cc;
                text-decoration: none;
            }}
            .file-link:hover {{
                text-decoration: underline;
            }}
        </style>
    </head>
    <body>
        <h1>Call Logs</h1>
        <p>Total Files: {len(files_info)}</p>
        <table>
            <tr>
                <th>Filename</th>
                <th>Last Modified</th>
                <th>Size</th>
            </tr>
            {''.join(f"""
            <tr>
                <td><a href="/logs/{file['filename']}" class="file-link">{file['filename']}</a></td>
                <td>{file['modified'].strftime('%Y-%m-%d %H:%M:%S')}</td>
                <td>{format_size(file['size'])}</td>
            </tr>
            """ for file in files_info)}
        </table>
    </body>
    </html>
    """
    
    return HTMLResponse(content=html_content)

@app.get("/logs/{filename}")
async def get_log_file(filename: str):
    """Return the contents of a specific log file."""
    file_path = f"calls/{filename}"
    if os.path.exists(file_path):
        return FileResponse(file_path)
    return JSONResponse(content={"error": "Log file not found"}, status_code=404)

@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """Handle WebSocket connections between Twilio and OpenAI."""
    print("Client connected")
    await websocket.accept()
    logger = None  # Initialize logger variable

    async with websockets.connect(
        'wss://api.openai.com/v1/realtime?model=gpt-4o-mini-realtime-preview-2024-12-17',
        additional_headers={
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
            nonlocal stream_sid, latest_media_timestamp, logger
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data['event'] == 'media':
                        latest_media_timestamp = int(data['media']['timestamp'])
                        audio_append = {
                            "type": "input_audio_buffer.append",
                            "audio": data['media']['payload']
                        }
                        await openai_ws.send(json.dumps(audio_append))
                    elif data['event'] == 'start':
                        stream_sid = data['start']['streamSid']
                        logger = setup_logging(stream_sid)
                        logger.info(f"Incoming stream started: {stream_sid}")
                        print(f"Incoming stream has started {stream_sid}")
                        response_start_timestamp_twilio = None
                        latest_media_timestamp = 0
                        last_assistant_item = None
                    elif data['event'] == 'mark':
                        if logger:
                            logger.info(f"Mark event received: {data}")
                        if mark_queue:
                            mark_queue.pop(0)
            except WebSocketDisconnect:
                if logger:
                    logger.info("Client disconnected.")
                print("Client disconnected.")
                await openai_ws.close()

        async def send_to_twilio():
            """Receive events from the OpenAI Realtime API, send audio back to Twilio."""
            nonlocal stream_sid, last_assistant_item, response_start_timestamp_twilio
            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)
                    if response['type'] in LOG_EVENT_TYPES:
                        if logger:
                            logger.info(f"OpenAI event: {response['type']}", extra=response)
                        print(f"Received event: {response['type']}", response)

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
                if logger:
                    logger.error(f"Error in send_to_twilio: {e}")
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
                    "text": "Greet the user with 'Welcome to Snakescript. I am Sage, an AI empowered agent. How can I help you today?'"
                }
            ]
        }
    }
    await openai_ws.send(json.dumps(initial_conversation_item))
    await openai_ws.send(json.dumps({"type": "response.create"}))


async def initialize_session(openai_ws):
    """Control initial session with OpenAI."""
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
    print('Sending session update:', json.dumps(session_update))
    await openai_ws.send(json.dumps(session_update))
    
    # Add a small delay to ensure session is initialized
    await asyncio.sleep(0.5)
    
    # Always send initial greeting
    await send_initial_conversation_item(openai_ws)

# Add logging configuration
def setup_logging(stream_sid):
    """Setup logging for a specific call stream."""
    log_filename = f"calls/{datetime.now().strftime('%Y%m%d')}_{stream_sid}.log"
    os.makedirs('calls', exist_ok=True)
    
    logger = logging.getLogger(stream_sid)
    logger.setLevel(logging.INFO)
    
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    
    file_handler = logging.FileHandler(log_filename)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    return logger

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
