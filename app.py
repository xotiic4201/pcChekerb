from fastapi import FastAPI, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
import socketio
import asyncio
import os
import uuid
import json
import subprocess
import sys
import psutil
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
import logging
import shutil
from pathlib import Path
import time
import threading

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI
app = FastAPI(
    title="Discord Bot Hosting API",
    version="1.0.0",
    description="Backend API for hosting Discord bots"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Socket.IO
sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins='*',
    ping_timeout=60,
    ping_interval=25,
    logger=True,
    engineio_logger=True
)

# Create necessary directories
UPLOAD_DIR = "uploads"
BOTS_DIR = "bots"
LOGS_DIR = "logs"

for directory in [UPLOAD_DIR, BOTS_DIR, LOGS_DIR]:
    Path(directory).mkdir(exist_ok=True)

# Mount static files
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/bots", StaticFiles(directory=BOTS_DIR), name="bots")

# In-memory storage
connected_clients: Dict[str, dict] = {}  # client_id -> client_info
bots_registry: Dict[str, dict] = {}      # bot_id -> bot_info
bot_processes: Dict[str, subprocess.Popen] = {}  # bot_id -> process
bot_logs: Dict[str, List[dict]] = {}     # bot_id -> logs
client_connections: Dict[str, str] = {}  # sid -> client_id
active_websockets: Set[WebSocket] = set()

class BotManager:
    def __init__(self):
        self.bots = {}
        self.client_connections = {}
        self.running = True
        
    async def register_client(self, client_id: str, sid: str, client_info: dict = None):
        """Register a new client (laptop/device) connection"""
        self.client_connections[client_id] = sid
        connected_clients[client_id] = {
            'sid': sid,
            'connected_at': datetime.now().isoformat(),
            'last_seen': datetime.now().isoformat(),
            'ip': client_info.get('ip', 'unknown') if client_info else 'unknown',
            'platform': client_info.get('platform', 'unknown') if client_info else 'unknown',
            'bot_count': 0
        }
        client_connections[sid] = client_id
        logger.info(f"Client registered: {client_id}")
        
        # Send confirmation
        await sio.emit('registration_confirmed', {
            'client_id': client_id,
            'message': 'Successfully connected to server',
            'server_time': datetime.now().isoformat()
        }, room=sid)
        
        # Broadcast client count update
        await self.broadcast_client_count()
    
    async def unregister_client(self, client_id: str):
        """Remove client connection"""
        if client_id in self.client_connections:
            sid = self.client_connections[client_id]
            del self.client_connections[client_id]
            if sid in client_connections:
                del client_connections[sid]
        
        if client_id in connected_clients:
            # Stop all bots from this client
            for bot_id, bot in list(bots_registry.items()):
                if bot.get('client_id') == client_id:
                    await self.stop_bot_process(bot_id)
            
            del connected_clients[client_id]
        
        await self.broadcast_client_count()
    
    async def broadcast_client_count(self):
        """Broadcast updated client count to all"""
        await sio.emit('clients_update', {
            'total_clients': len(connected_clients),
            'clients': list(connected_clients.keys())
        })
    
    async def update_bot_status(self, bot_data: dict):
        """Update bot status and broadcast to all clients"""
        bot_id = bot_data.get('id')
        if not bot_id:
            return
        
        # Update or create bot entry
        if bot_id not in bots_registry:
            bots_registry[bot_id] = {
                'id': bot_id,
                'name': bot_data.get('name', 'Unnamed Bot'),
                'status': 'unknown',
                'client_id': bot_data.get('client_id'),
                'created_at': datetime.now().isoformat(),
                'last_updated': datetime.now().isoformat(),
                'type': bot_data.get('type', 'unknown'),
                'cpu': 0,
                'memory': 0,
                'uptime': '0',
                'logs': [],
                'config': bot_data.get('config', {})
            }
        
        # Update fields
        bots_registry[bot_id].update({
            'status': bot_data.get('status', bots_registry[bot_id]['status']),
            'last_updated': datetime.now().isoformat(),
            'cpu': bot_data.get('cpu', bots_registry[bot_id]['cpu']),
            'memory': bot_data.get('memory', bots_registry[bot_id]['memory']),
            'uptime': bot_data.get('uptime', bots_registry[bot_id]['uptime'])
        })
        
        # Add to logs
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'level': 'info',
            'message': f"Status: {bot_data.get('status')}",
            'data': bot_data
        }
        
        bot_logs.setdefault(bot_id, []).append(log_entry)
        
        # Keep only last 100 logs per bot
        if len(bot_logs[bot_id]) > 100:
            bot_logs[bot_id] = bot_logs[bot_id][-100:]
        
        # Broadcast update to all connected clients
        await sio.emit('bot_update', {
            'bot': bots_registry[bot_id],
            'total_bots': len(bots_registry),
            'timestamp': datetime.now().isoformat()
        })
    
    async def start_bot_process(self, bot_id: str):
        """Start a bot process locally (for testing)"""
        bot = bots_registry.get(bot_id)
        if not bot:
            return False
        
        try:
            bot_path = os.path.join(BOTS_DIR, bot_id)
            
            if bot['type'] == 'python':
                # For Python bots
                if os.path.exists(os.path.join(bot_path, 'requirements.txt')):
                    subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', 
                                   os.path.join(bot_path, 'requirements.txt')], 
                                  capture_output=True)
                
                # Find main python file
                py_files = [f for f in os.listdir(bot_path) if f.endswith('.py')]
                if py_files:
                    main_file = py_files[0]
                    process = subprocess.Popen(
                        [sys.executable, os.path.join(bot_path, main_file)],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        env={**os.environ, 'DISCORD_BOT_ID': bot_id}
                    )
                    bot_processes[bot_id] = process
                    
                    # Start log reader thread
                    threading.Thread(target=self._read_bot_logs, args=(bot_id, process), daemon=True).start()
                    
                    return True
                    
            elif bot['type'] == 'nodejs':
                # For Node.js bots
                if os.path.exists(os.path.join(bot_path, 'package.json')):
                    subprocess.run(['npm', 'install'], cwd=bot_path, capture_output=True)
                
                # Find main js file
                js_files = [f for f in os.listdir(bot_path) if f.endswith('.js')]
                if js_files:
                    main_file = js_files[0]
                    process = subprocess.Popen(
                        ['node', os.path.join(bot_path, main_file)],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        env={**os.environ, 'DISCORD_BOT_ID': bot_id}
                    )
                    bot_processes[bot_id] = process
                    
                    # Start log reader thread
                    threading.Thread(target=self._read_bot_logs, args=(bot_id, process), daemon=True).start()
                    
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error starting bot {bot_id}: {e}")
            return False
    
    def _read_bot_logs(self, bot_id: str, process: subprocess.Popen):
        """Read logs from bot process"""
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            
            if line:
                log_entry = {
                    'timestamp': datetime.now().isoformat(),
                    'level': 'info',
                    'message': line.strip()
                }
                
                bot_logs.setdefault(bot_id, []).append(log_entry)
                
                # Broadcast log update
                asyncio.run_coroutine_threadsafe(
                    sio.emit('bot_log', {
                        'bot_id': bot_id,
                        'log': log_entry
                    }),
                    asyncio.get_event_loop()
                )
    
    async def stop_bot_process(self, bot_id: str):
        """Stop a running bot process"""
        if bot_id in bot_processes:
            process = bot_processes[bot_id]
            
            try:
                # Try graceful termination
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # Force kill if not responding
                    process.kill()
                    process.wait()
            except:
                pass
            
            del bot_processes[bot_id]
            
            # Update status
            if bot_id in bots_registry:
                bots_registry[bot_id]['status'] = 'stopped'
                bots_registry[bot_id]['cpu'] = 0
                bots_registry[bot_id]['memory'] = 0
                
                await sio.emit('bot_update', {
                    'bot': bots_registry[bot_id],
                    'total_bots': len(bots_registry)
                })
            
            return True
        return False
    
    async def send_command_to_client(self, client_id: str, command: dict) -> bool:
        """Send command to specific client (laptop)"""
        if client_id in self.client_connections:
            await sio.emit('bot_command', command, room=self.client_connections[client_id])
            return True
        return False
    
    def get_all_bots(self) -> List[dict]:
        """Get all bots data"""
        return list(bots_registry.values())
    
    def get_bot_stats(self) -> dict:
        """Get overall bot statistics"""
        total = len(bots_registry)
        running = sum(1 for b in bots_registry.values() if b.get('status') == 'running')
        stopped = sum(1 for b in bots_registry.values() if b.get('status') == 'stopped')
        error = sum(1 for b in bots_registry.values() if b.get('status') == 'error')
        
        total_cpu = sum(b.get('cpu', 0) for b in bots_registry.values())
        total_memory = sum(b.get('memory', 0) for b in bots_registry.values())
        
        return {
            'total': total,
            'running': running,
            'stopped': stopped,
            'error': error,
            'total_cpu': total_cpu,
            'total_memory': total_memory
        }

# Initialize bot manager
manager = BotManager()

# Socket.IO events
@sio.event
async def connect(sid, environ):
    logger.info(f"Client connected: {sid}")
    await sio.emit('welcome', {
        'message': 'Connected to Discord Bot Hosting Server',
        'server_time': datetime.now().isoformat(),
        'sid': sid
    }, room=sid)

@sio.event
async def disconnect(sid):
    logger.info(f"Client disconnected: {sid}")
    # Find and remove disconnected client
    client_id = client_connections.get(sid)
    if client_id:
        await manager.unregister_client(client_id)
        logger.info(f"Client disconnected: {client_id}")
    elif sid in client_connections:
        del client_connections[sid]

@sio.event
async def register_client(sid, data):
    """Register a new client (laptop)"""
    client_id = data.get('client_id', str(uuid.uuid4()))
    client_info = {
        'ip': data.get('ip', 'unknown'),
        'platform': data.get('platform', 'unknown'),
        'version': data.get('version', '1.0.0')
    }
    await manager.register_client(client_id, sid, client_info)

@sio.event
async def bot_status_update(sid, data):
    """Update bot status from client"""
    await manager.update_bot_status(data)

@sio.event
async def bot_heartbeat(sid, data):
    """Receive heartbeat from client"""
    client_id = client_connections.get(sid)
    if client_id and client_id in connected_clients:
        connected_clients[client_id]['last_seen'] = datetime.now().isoformat()
        connected_clients[client_id]['bot_count'] = data.get('bot_count', 0)
    
    # Acknowledge heartbeat
    await sio.emit('heartbeat_ack', {
        'timestamp': datetime.now().isoformat()
    }, room=sid)

@sio.event
async def bot_log_update(sid, data):
    """Receive log update from bot"""
    bot_id = data.get('bot_id')
    log_entry = data.get('log')
    
    if bot_id and log_entry:
        bot_logs.setdefault(bot_id, []).append({
            'timestamp': datetime.now().isoformat(),
            'level': log_entry.get('level', 'info'),
            'message': log_entry.get('message', ''),
            'source': 'bot'
        })
        
        # Broadcast to all clients monitoring this bot
        await sio.emit('bot_log', {
            'bot_id': bot_id,
            'log': log_entry
        })

# Bot control events
@sio.event
async def start_bot(sid, data):
    """Request to start a bot"""
    bot_id = data.get('bot_id')
    if bot_id in bots_registry:
        bot = bots_registry[bot_id]
        client_id = bot.get('client_id')
        
        if client_id:
            # Send command to client
            await manager.send_command_to_client(client_id, {
                'action': 'start',
                'bot_id': bot_id,
                'timestamp': datetime.now().isoformat()
            })
        else:
            # Start locally if no client assigned
            await manager.start_bot_process(bot_id)

@sio.event
async def stop_bot(sid, data):
    """Request to stop a bot"""
    bot_id = data.get('bot_id')
    if bot_id in bots_registry:
        bot = bots_registry[bot_id]
        client_id = bot.get('client_id')
        
        if client_id:
            # Send command to client
            await manager.send_command_to_client(client_id, {
                'action': 'stop',
                'bot_id': bot_id,
                'timestamp': datetime.now().isoformat()
            })
        else:
            # Stop locally if no client assigned
            await manager.stop_bot_process(bot_id)

@sio.event
async def restart_bot(sid, data):
    """Request to restart a bot"""
    bot_id = data.get('bot_id')
    if bot_id in bots_registry:
        bot = bots_registry[bot_id]
        client_id = bot.get('client_id')
        
        if client_id:
            # Send command to client
            await manager.send_command_to_client(client_id, {
                'action': 'restart',
                'bot_id': bot_id,
                'timestamp': datetime.now().isoformat()
            })

@sio.event
async def deploy_bot(sid, data):
    """Handle new bot deployment request"""
    bot_name = data.get('name', 'New Bot')
    bot_type = data.get('type', 'python')
    token = data.get('token', '')
    code = data.get('code', '')
    client_id = data.get('client_id')
    
    if not client_id:
        # Assign to client with least bots
        if connected_clients:
            client_id = min(connected_clients.keys(), 
                          key=lambda x: connected_clients[x].get('bot_count', 0))
    
    if client_id and client_id in connected_clients:
        bot_id = str(uuid.uuid4())
        
        # Create bot entry
        bots_registry[bot_id] = {
            'id': bot_id,
            'name': bot_name,
            'status': 'deploying',
            'client_id': client_id,
            'created_at': datetime.now().isoformat(),
            'last_updated': datetime.now().isoformat(),
            'type': bot_type,
            'cpu': 0,
            'memory': 0,
            'uptime': '0',
            'logs': [],
            'config': {
                'has_token': bool(token),
                'code_provided': bool(code)
            }
        }
        
        # Send deployment command to client
        success = await manager.send_command_to_client(client_id, {
            'action': 'deploy',
            'bot_id': bot_id,
            'bot_data': {
                'name': bot_name,
                'type': bot_type,
                'token': token,
                'code': code
            },
            'timestamp': datetime.now().isoformat()
        })
        
        if success:
            await sio.emit('bot_deployed', {
                'bot_id': bot_id,
                'message': 'Bot deployment started',
                'client_id': client_id
            }, room=sid)
        else:
            await sio.emit('deployment_error', {
                'error': 'Failed to send deployment command to client',
                'bot_id': bot_id
            }, room=sid)
    else:
        await sio.emit('deployment_error', {
            'error': 'No connected clients available',
            'bot_id': None
        }, room=sid)

# Mount Socket.IO app
socket_app = socketio.ASGIApp(sio, app)

# WebSocket endpoint for direct communication
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_websockets.add(websocket)
    
    try:
        while True:
            data = await websocket.receive_json()
            # Handle WebSocket messages if needed
            await websocket.send_json({
                "status": "received", 
                "data": data,
                "timestamp": datetime.now().isoformat()
            })
    except WebSocketDisconnect:
        active_websockets.remove(websocket)
        logger.info("WebSocket disconnected")

# REST API endpoints
@app.get("/")
async def root():
    return {
        "message": "Discord Bot Hosting API",
        "status": "online",
        "version": "1.0.0",
        "timestamp": datetime.now().isoformat(),
        "endpoints": {
            "bots": "/api/bots",
            "system_stats": "/api/system/stats",
            "clients": "/api/clients",
            "health": "/api/health",
            "docs": "/docs",
            "redoc": "/redoc"
        }
    }

@app.get("/api/bots")
async def get_bots(status: Optional[str] = None):
    """Get all bots, optionally filtered by status"""
    bots = manager.get_all_bots()
    
    if status:
        bots = [bot for bot in bots if bot.get('status') == status]
    
    return JSONResponse({
        'bots': bots,
        'total': len(bots),
        'stats': manager.get_bot_stats(),
        'timestamp': datetime.now().isoformat()
    })

@app.get("/api/bot/{bot_id}")
async def get_bot(bot_id: str):
    """Get specific bot details"""
    if bot_id in bots_registry:
        bot = bots_registry[bot_id].copy()
        bot['logs'] = bot_logs.get(bot_id, [])[-50:]  # Last 50 logs
        return bot
    raise HTTPException(status_code=404, detail="Bot not found")

@app.get("/api/bot/{bot_id}/logs")
async def get_bot_logs(bot_id: str, limit: int = 100):
    """Get bot logs"""
    if bot_id in bot_logs:
        logs = bot_logs[bot_id][-limit:] if limit > 0 else bot_logs[bot_id]
        return {'logs': logs, 'total': len(logs)}
    return {'logs': [], 'total': 0}

@app.delete("/api/bot/{bot_id}")
async def delete_bot(bot_id: str):
    """Delete a bot"""
    if bot_id in bots_registry:
        # Stop bot if running
        await manager.stop_bot_process(bot_id)
        
        # Remove from registry
        del bots_registry[bot_id]
        
        # Remove logs
        if bot_id in bot_logs:
            del bot_logs[bot_id]
        
        return {"message": "Bot deleted successfully", "bot_id": bot_id}
    raise HTTPException(status_code=404, detail="Bot not found")

@app.post("/api/upload")
async def upload_bot(
    file: UploadFile = File(...),
    name: str = Form(...),
    token: str = Form(None),
    bot_type: str = Form("python")
):
    """Handle bot file upload"""
    try:
        # Generate unique ID
        upload_id = str(uuid.uuid4())
        
        # Save file
        filename = f"{upload_id}_{file.filename}"
        filepath = os.path.join(UPLOAD_DIR, filename)
        
        with open(filepath, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Create bot entry
        bot_id = str(uuid.uuid4())
        bots_registry[bot_id] = {
            'id': bot_id,
            'name': name,
            'status': 'uploaded',
            'client_id': None,
            'created_at': datetime.now().isoformat(),
            'type': bot_type,
            'cpu': 0,
            'memory': 0,
            'uptime': '0',
            'file_path': filepath,
            'original_filename': file.filename,
            'config': {
                'has_token': bool(token),
                'uploaded': True
            }
        }
        
        # Broadcast new bot
        await sio.emit('bot_update', {
            'bot': bots_registry[bot_id],
            'total_bots': len(bots_registry)
        })
        
        return {
            'success': True,
            'message': 'Bot uploaded successfully',
            'bot_id': bot_id,
            'filename': filename,
            'url': f'/uploads/{filename}',
            'upload_id': upload_id
        }
    except Exception as e:
        logger.error(f"Upload error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/system/stats")
async def system_stats():
    """Get system statistics"""
    total_bots = len(bots_registry)
    running_bots = sum(1 for b in bots_registry.values() if b.get('status') == 'running')
    
    # Calculate resource usage
    total_cpu = sum(b.get('cpu', 0) for b in bots_registry.values())
    total_memory = sum(b.get('memory', 0) for b in bots_registry.values())
    
    # Get server stats
    cpu_percent = psutil.cpu_percent()
    memory = psutil.virtual_memory()
    
    return {
        'bots': {
            'total': total_bots,
            'running': running_bots,
            'stopped': total_bots - running_bots,
            'total_cpu': total_cpu,
            'total_memory': total_memory
        },
        'clients': {
            'total': len(connected_clients),
            'list': list(connected_clients.keys())
        },
        'server': {
            'cpu': cpu_percent,
            'memory': {
                'total': memory.total / (1024**3),  # GB
                'used': memory.used / (1024**3),
                'percent': memory.percent
            },
            'uptime': time.time() - psutil.boot_time(),
            'timestamp': datetime.now().isoformat()
        },
        'connections': {
            'websocket': len(active_websockets),
            'socketio': len(client_connections),
            'total': len(active_websockets) + len(client_connections)
        }
    }

@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "bots": len(bots_registry),
        "clients": len(connected_clients),
        "uptime": time.time() - psutil.boot_time()
    }

@app.get("/api/clients")
async def get_clients():
    """Get connected clients"""
    clients_list = []
    
    for client_id, client_data in connected_clients.items():
        client_info = {
            "id": client_id,
            "connected_at": client_data.get("connected_at"),
            "last_seen": client_data.get("last_seen"),
            "bot_count": client_data.get("bot_count", 0),
            "platform": client_data.get("platform", "unknown"),
            "sid": client_data.get("sid", "unknown")[:8] + "..."
        }
        
        # Calculate if client is active (seen in last 60 seconds)
        last_seen = datetime.fromisoformat(client_data.get("last_seen", datetime.now().isoformat()))
        client_info["active"] = (datetime.now() - last_seen).total_seconds() < 60
        
        clients_list.append(client_info)
    
    return {
        "clients": clients_list,
        "total": len(clients_list),
        "active": sum(1 for c in clients_list if c.get("active", False))
    }

@app.get("/api/download/{bot_id}")
async def download_bot_files(bot_id: str):
    """Download bot files"""
    if bot_id in bots_registry:
        bot = bots_registry[bot_id]
        file_path = bot.get('file_path')
        
        if file_path and os.path.exists(file_path):
            return FileResponse(
                path=file_path,
                filename=bot.get('original_filename', 'bot_files.zip'),
                media_type='application/octet-stream'
            )
    
    raise HTTPException(status_code=404, detail="Bot files not found")

# Cleanup task for inactive clients
async def cleanup_inactive_clients():
    """Periodically clean up inactive clients"""
    while True:
        await asyncio.sleep(60)  # Run every minute
        
        now = datetime.now()
        inactive_clients = []
        
        for client_id, client_data in connected_clients.items():
            last_seen = datetime.fromisoformat(client_data.get("last_seen", datetime.now().isoformat()))
            
            # If client hasn't been seen in 5 minutes, mark as inactive
            if (now - last_seen).total_seconds() > 300:  # 5 minutes
                inactive_clients.append(client_id)
        
        # Remove inactive clients
        for client_id in inactive_clients:
            logger.warning(f"Removing inactive client: {client_id}")
            await manager.unregister_client(client_id)

# Startup event
@app.on_event("startup")
async def startup_event():
    """Startup tasks"""
    logger.info("Starting Discord Bot Hosting Server")
    
    # Start cleanup task
    asyncio.create_task(cleanup_inactive_clients())
    
    logger.info(f"Server started at {datetime.now().isoformat()}")
    logger.info(f"Upload directory: {UPLOAD_DIR}")
    logger.info(f"Bots directory: {BOTS_DIR}")
    logger.info(f"Logs directory: {LOGS_DIR}")

# Shutdown event
@app.on_event("shutdown")
async def shutdown_event():
    """Shutdown tasks"""
    logger.info("Shutting down Discord Bot Hosting Server")
    
    # Stop all running bots
    for bot_id in list(bot_processes.keys()):
        await manager.stop_bot_process(bot_id)
    
    logger.info("Server shutdown complete")

# For Render deployment
if __name__ == "__main__":
    import uvicorn
    
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")
    
    logger.info(f"Starting server on {host}:{port}")
    
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=False,  # Set to True for development
        log_level="info"
    )
