from fastapi import FastAPI, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import socketio
import asyncio
import os
import uuid
import json
from datetime import datetime
from typing import Dict, List, Optional
import logging
import shutil
from pathlib import Path

# Initialize FastAPI
app = FastAPI(title="Discord Bot Hosting API", version="1.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Socket.IO
sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins='*',
    ping_timeout=60,
    ping_interval=25
)

# Mount Socket.IO app
socket_app = socketio.ASGIApp(sio, app)

# In-memory storage (use Redis in production)
connected_laptops: Dict[str, dict] = {}
bots_registry: Dict[str, dict] = {}
bot_logs: Dict[str, List[dict]] = {}
client_connections: Dict[str, str] = {}  # sid -> laptop_id

class BotManager:
    def __init__(self):
        self.bots = {}
        self.laptop_connections = {}
    
    async def register_laptop(self, laptop_id: str, sid: str):
        """Register a new laptop connection"""
        self.laptop_connections[laptop_id] = sid
        connected_laptops[laptop_id] = {
            'sid': sid,
            'connected_at': datetime.now().isoformat(),
            'bot_count': 0
        }
        client_connections[sid] = laptop_id
        print(f"Laptop registered: {laptop_id}")
    
    async def unregister_laptop(self, laptop_id: str):
        """Remove laptop connection"""
        if laptop_id in self.laptop_connections:
            sid = self.laptop_connections[laptop_id]
            del self.laptop_connections[laptop_id]
            if sid in client_connections:
                del client_connections[sid]
        
        if laptop_id in connected_laptops:
            del connected_laptops[laptop_id]
    
    async def update_bot_status(self, bot_data: dict):
        """Update bot status and broadcast to all clients"""
        bot_id = bot_data.get('id')
        if bot_id:
            bots_registry[bot_id] = bot_data
            bot_logs.setdefault(bot_id, []).append({
                'timestamp': datetime.now().isoformat(),
                'message': bot_data.get('status', ''),
                'data': bot_data
            })
            
            # Keep only last 100 logs
            if len(bot_logs[bot_id]) > 100:
                bot_logs[bot_id] = bot_logs[bot_id][-100:]
            
            # Broadcast update to all connected clients
            await sio.emit('bot_update', {
                'bot': bot_data,
                'total_bots': len(bots_registry)
            })
    
    def get_all_bots(self) -> List[dict]:
        """Get all bots data"""
        return list(bots_registry.values())
    
    async def send_command_to_laptop(self, laptop_id: str, command: dict) -> bool:
        """Send command to specific laptop"""
        if laptop_id in self.laptop_connections:
            await sio.emit('bot_command', command, room=self.laptop_connections[laptop_id])
            return True
        return False

manager = BotManager()

# Socket.IO events
@sio.event
async def connect(sid, environ):
    print(f"Client connected: {sid}")

@sio.event
async def disconnect(sid):
    print(f"Client disconnected: {sid}")
    # Find and remove disconnected laptop
    laptop_id = client_connections.get(sid)
    if laptop_id:
        await manager.unregister_laptop(laptop_id)
        print(f"Laptop disconnected: {laptop_id}")
    elif sid in client_connections:
        del client_connections[sid]

@sio.event
async def register_laptop(sid, data):
    laptop_id = data.get('laptop_id', str(uuid.uuid4()))
    await manager.register_laptop(laptop_id, sid)
    await sio.emit('registration_confirmed', {'laptop_id': laptop_id}, room=sid)

@sio.event
async def bot_status_update(sid, data):
    await manager.update_bot_status(data)

@sio.event
async def start_bot(sid, data):
    bot_id = data.get('bot_id')
    if bot_id in bots_registry:
        bot = bots_registry[bot_id]
        laptop_id = bot.get('laptop_id')
        if laptop_id:
            await manager.send_command_to_laptop(laptop_id, {
                'action': 'start',
                'bot_id': bot_id
            })

@sio.event
async def stop_bot(sid, data):
    bot_id = data.get('bot_id')
    if bot_id in bots_registry:
        bot = bots_registry[bot_id]
        laptop_id = bot.get('laptop_id')
        if laptop_id:
            await manager.send_command_to_laptop(laptop_id, {
                'action': 'stop',
                'bot_id': bot_id
            })

@sio.event
async def restart_bot(sid, data):
    bot_id = data.get('bot_id')
    if bot_id in bots_registry:
        bot = bots_registry[bot_id]
        laptop_id = bot.get('laptop_id')
        if laptop_id:
            await manager.send_command_to_laptop(laptop_id, {
                'action': 'restart',
                'bot_id': bot_id
            })

@sio.event
async def deploy_bot(sid, data):
    """Handle new bot deployment"""
    bot_name = data.get('name', 'New Bot')
    laptop_id = data.get('laptop_id')
    
    if not laptop_id:
        # Assign to laptop with least bots
        if connected_laptops:
            laptop_id = min(connected_laptops.keys(), 
                          key=lambda x: connected_laptops[x]['bot_count'])
    
    if laptop_id and laptop_id in connected_laptops:
        bot_id = str(uuid.uuid4())
        
        # Create bot entry
        bots_registry[bot_id] = {
            'id': bot_id,
            'name': bot_name,
            'status': 'deploying',
            'laptop_id': laptop_id,
            'created_at': datetime.now().isoformat(),
            'logs': [],
            'type': data.get('type', 'python'),
            'cpu': 0,
            'memory': 0,
            'uptime': '0'
        }
        
        # Send deployment command to laptop
        await manager.send_command_to_laptop(laptop_id, {
            'action': 'deploy',
            'bot_id': bot_id,
            'bot_data': data
        })
        
        await sio.emit('bot_deployed', {'bot_id': bot_id, 'message': 'Bot deployment started'}, room=sid)

# Create uploads directory
UPLOAD_DIR = "uploads"
Path(UPLOAD_DIR).mkdir(exist_ok=True)

# Mount static files for uploads
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# REST API endpoints
@app.get("/")
async def root():
    return {"message": "Discord Bot Hosting API", "status": "online"}

@app.get("/api/bots")
async def get_bots():
    return JSONResponse({
        'bots': manager.get_all_bots(),
        'total': len(bots_registry)
    })

@app.get("/api/bot/{bot_id}")
async def get_bot(bot_id: str):
    if bot_id in bots_registry:
        return bots_registry[bot_id]
    raise HTTPException(status_code=404, detail="Bot not found")

@app.get("/api/bot/{bot_id}/logs")
async def get_bot_logs(bot_id: str):
    if bot_id in bot_logs:
        return {'logs': bot_logs[bot_id]}
    return {'logs': []}

@app.post("/api/upload")
async def upload_bot(
    file: UploadFile = File(...),
    name: str = Form(...),
    token: str = Form(None),
    bot_type: str = Form("python")
):
    """Handle bot file upload"""
    try:
        # Save file
        upload_dir = Path(UPLOAD_DIR)
        upload_dir.mkdir(exist_ok=True)
        
        filename = f"{uuid.uuid4()}_{file.filename}"
        filepath = upload_dir / filename
        
        with open(filepath, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Process for deployment
        await sio.emit('new_bot_upload', {
            'filename': filename,
            'name': name,
            'path': str(filepath),
            'type': bot_type,
            'token': token
        })
        
        return {
            'success': True,
            'message': 'Bot uploaded successfully',
            'filename': filename,
            'url': f'/uploads/{filename}'
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/system/stats")
async def system_stats():
    """Get system statistics"""
    total_bots = len(bots_registry)
    running_bots = sum(1 for b in bots_registry.values() if b.get('status') == 'running')
    
    return {
        'total_bots': total_bots,
        'running_bots': running_bots,
        'connected_laptops': len(connected_laptops),
        'uptime': datetime.now().isoformat(),
        'server_time': datetime.now().isoformat()
    }

@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "bots": len(bots_registry),
        "laptops": len(connected_laptops)
    }

@app.get("/api/laptops")
async def get_laptops():
    """Get connected laptops"""
    return {
        "laptops": [
            {
                "id": lid,
                "connected_at": data.get("connected_at"),
                "bot_count": data.get("bot_count", 0)
            }
            for lid, data in connected_laptops.items()
        ]
    }

# WebSocket endpoint for direct communication
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            # Handle WebSocket messages if needed
            await websocket.send_json({"status": "received", "data": data})
    except WebSocketDisconnect:
        print("WebSocket disconnected")

# For Render deployment
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=True
    )
