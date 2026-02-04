from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
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
import zipfile

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize FastAPI
app = FastAPI(
    title="xotiicBotHosting API",
    version="2.0.0",
    description="Premium Discord Bot Hosting Platform Backend"
)

# CORS middleware - Allow all origins for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Socket.IO with proper CORS
sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins='*',
    ping_timeout=120,
    ping_interval=30,
    logger=True,
    engineio_logger=False
)

# Create necessary directories
UPLOAD_DIR = "uploads"
BOTS_DIR = "bots"
LOGS_DIR = "logs"
STATIC_DIR = "static"

for directory in [UPLOAD_DIR, BOTS_DIR, LOGS_DIR, STATIC_DIR]:
    Path(directory).mkdir(exist_ok=True)

# Mount static files
try:
    app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
    app.mount("/bots", StaticFiles(directory=BOTS_DIR), name="bots")
except Exception as e:
    logger.warning(f"Could not mount static directories: {e}")

# In-memory storage
connected_clients: Dict[str, dict] = {}
connected_workers: Dict[str, dict] = {}
bots_registry: Dict[str, dict] = {}
bot_processes: Dict[str, subprocess.Popen] = {}
bot_logs: Dict[str, List[dict]] = {}
client_connections: Dict[str, str] = {}

# Track bot start times for uptime calculation
bot_start_times: Dict[str, datetime] = {}

class BotManager:
    def __init__(self):
        self.bots = bots_registry
        self.processes = bot_processes
        self.running = True
        
    def calculate_uptime(self, bot_id: str) -> str:
        """Calculate bot uptime"""
        if bot_id not in bot_start_times:
            return "0s"
        
        uptime_seconds = (datetime.now() - bot_start_times[bot_id]).total_seconds()
        
        if uptime_seconds < 60:
            return f"{int(uptime_seconds)}s"
        elif uptime_seconds < 3600:
            return f"{int(uptime_seconds / 60)}m"
        elif uptime_seconds < 86400:
            hours = int(uptime_seconds / 3600)
            minutes = int((uptime_seconds % 3600) / 60)
            return f"{hours}h {minutes}m"
        else:
            days = int(uptime_seconds / 86400)
            hours = int((uptime_seconds % 86400) / 3600)
            return f"{days}d {hours}h"
    
    async def update_bot_metrics(self, bot_id: str):
        """Update bot metrics (CPU, Memory, Uptime)"""
        if bot_id not in self.bots:
            return
        
        bot = self.bots[bot_id]
        
        # Calculate uptime
        if bot.get('status') == 'running':
            bot['uptime'] = self.calculate_uptime(bot_id)
            
            # Get process metrics if available
            if bot_id in self.processes:
                try:
                    process = self.processes[bot_id]
                    p = psutil.Process(process.pid)
                    bot['cpu'] = round(p.cpu_percent(interval=0.1), 1)
                    bot['memory'] = round(p.memory_info().rss / (1024 * 1024), 1)  # MB
                except:
                    pass
        else:
            bot['cpu'] = 0
            bot['memory'] = 0
            bot['uptime'] = '0s'
        
        bot['last_updated'] = datetime.now().isoformat()
    
    async def create_bot_entry(self, name: str, bot_type: str, token: str = None, code: str = None) -> str:
        """Create a new bot entry"""
        bot_id = str(uuid.uuid4())
        
        self.bots[bot_id] = {
            'id': bot_id,
            'name': name,
            'status': 'stopped',
            'type': bot_type,
            'created_at': datetime.now().isoformat(),
            'last_updated': datetime.now().isoformat(),
            'cpu': 0,
            'memory': 0,
            'uptime': '0s',
            'token': token,
            'code': code,
            'logs': []
        }
        
        # Initialize logs
        bot_logs[bot_id] = [{
            'timestamp': datetime.now().isoformat(),
            'level': 'info',
            'message': f'Bot "{name}" created successfully'
        }]
        
        logger.info(f"Created bot: {bot_id} - {name}")
        return bot_id
    
    async def broadcast_bot_update(self, bot_id: str):
        """Broadcast bot update to all connected clients"""
        if bot_id in self.bots:
            await sio.emit('bot_update', {
                'bot_id': bot_id,
                'bot': self.bots[bot_id]
            })
    
    async def start_bot(self, bot_id: str) -> bool:
        """Start a bot"""
        if bot_id not in self.bots:
            logger.error(f"Bot not found: {bot_id}")
            return False
        
        bot = self.bots[bot_id]
        
        # Check if already running
        if bot.get('status') == 'running':
            logger.warning(f"Bot already running: {bot_id}")
            return True
        
        try:
            # Update status
            bot['status'] = 'starting'
            await self.broadcast_bot_update(bot_id)
            
            # Create bot directory
            bot_dir = os.path.join(BOTS_DIR, bot_id)
            os.makedirs(bot_dir, exist_ok=True)
            
            # Create bot file based on type
            if bot['type'] == 'python':
                bot_file = os.path.join(bot_dir, 'bot.py')
                
                # Use provided code or create basic template
                if bot.get('code'):
                    code = bot['code']
                else:
                    code = f"""
import discord
from discord.ext import commands
import os
import sys

TOKEN = '{bot.get('token', 'YOUR_TOKEN_HERE')}'

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'{{bot.user}} is now online!')
    print(f'Bot ID: {{bot.user.id}}')
    print(f'Connected to {{len(bot.guilds)}} guilds')

@bot.command()
async def ping(ctx):
    await ctx.send(f'Pong! {{round(bot.latency * 1000)}}ms')

@bot.command()
async def hello(ctx):
    await ctx.send(f'Hello {{ctx.author.mention}}!')

if __name__ == '__main__':
    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f'Error: {{e}}')
        sys.exit(1)
"""
                
                with open(bot_file, 'w') as f:
                    f.write(code)
                
                # Start the process
                process = subprocess.Popen(
                    [sys.executable, bot_file],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd=bot_dir,
                    bufsize=1,
                    universal_newlines=True
                )
                
            elif bot['type'] == 'nodejs':
                bot_file = os.path.join(bot_dir, 'bot.js')
                
                if bot.get('code'):
                    code = bot['code']
                else:
                    code = f"""
const {{ Client, GatewayIntentBits }} = require('discord.js');

const client = new Client({{
    intents: [
        GatewayIntentBits.Guilds,
        GatewayIntentBits.GuildMessages,
        GatewayIntentBits.MessageContent
    ]
}});

client.once('ready', () => {{
    console.log(`Logged in as ${{client.user.tag}}`);
    console.log(`Bot ID: ${{client.user.id}}`);
    console.log(`Connected to ${{client.guilds.cache.size}} guilds`);
}});

client.on('messageCreate', async message => {{
    if (message.content === '!ping') {{
        const sent = await message.reply('Pinging...');
        const timeDiff = sent.createdTimestamp - message.createdTimestamp;
        sent.edit(`Pong! Latency: ${{timeDiff}}ms`);
    }}
    
    if (message.content === '!hello') {{
        message.reply(`Hello ${{message.author}}!`);
    }}
}});

client.login('{bot.get('token', 'YOUR_TOKEN_HERE')}');
"""
                
                with open(bot_file, 'w') as f:
                    f.write(code)
                
                # Create package.json
                package_json = {
                    "name": bot['name'].lower().replace(' ', '-'),
                    "version": "1.0.0",
                    "main": "bot.js",
                    "dependencies": {
                        "discord.js": "^14.14.1"
                    }
                }
                
                with open(os.path.join(bot_dir, 'package.json'), 'w') as f:
                    json.dump(package_json, f, indent=2)
                
                # Install dependencies
                npm_install = subprocess.run(
                    ['npm', 'install'],
                    cwd=bot_dir,
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                
                if npm_install.returncode != 0:
                    logger.error(f"npm install failed: {npm_install.stderr}")
                
                # Start the process
                process = subprocess.Popen(
                    ['node', bot_file],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd=bot_dir,
                    bufsize=1,
                    universal_newlines=True
                )
            
            # Store process
            self.processes[bot_id] = process
            bot_start_times[bot_id] = datetime.now()
            
            # Update status
            bot['status'] = 'running'
            await self.broadcast_bot_update(bot_id)
            
            # Start log monitoring
            asyncio.create_task(self._monitor_bot_logs(bot_id, process))
            
            logger.info(f"Bot started: {bot['name']} (PID: {process.pid})")
            return True
            
        except Exception as e:
            logger.error(f"Error starting bot {bot_id}: {e}")
            bot['status'] = 'error'
            await self.broadcast_bot_update(bot_id)
            return False
    
    async def stop_bot(self, bot_id: str) -> bool:
        """Stop a bot"""
        if bot_id not in self.bots:
            return False
        
        bot = self.bots[bot_id]
        
        if bot_id in self.processes:
            try:
                process = self.processes[bot_id]
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                
                del self.processes[bot_id]
                if bot_id in bot_start_times:
                    del bot_start_times[bot_id]
                
                logger.info(f"Bot stopped: {bot['name']}")
            except Exception as e:
                logger.error(f"Error stopping bot {bot_id}: {e}")
        
        bot['status'] = 'stopped'
        bot['cpu'] = 0
        bot['memory'] = 0
        bot['uptime'] = '0s'
        await self.broadcast_bot_update(bot_id)
        
        return True
    
    async def restart_bot(self, bot_id: str) -> bool:
        """Restart a bot"""
        await self.stop_bot(bot_id)
        await asyncio.sleep(2)
        return await self.start_bot(bot_id)
    
    async def delete_bot(self, bot_id: str) -> bool:
        """Delete a bot"""
        if bot_id not in self.bots:
            return False
        
        # Stop if running
        await self.stop_bot(bot_id)
        
        # Delete files
        bot_dir = os.path.join(BOTS_DIR, bot_id)
        if os.path.exists(bot_dir):
            shutil.rmtree(bot_dir)
        
        # Remove from registry
        del self.bots[bot_id]
        if bot_id in bot_logs:
            del bot_logs[bot_id]
        
        logger.info(f"Bot deleted: {bot_id}")
        
        # Broadcast update
        await sio.emit('bot_deleted', {'bot_id': bot_id})
        
        return True
    
    async def _monitor_bot_logs(self, bot_id: str, process: subprocess.Popen):
        """Monitor bot logs and broadcast them"""
        try:
            while process.poll() is None:
                line = process.stdout.readline()
                if line:
                    line = line.strip()
                    if line:
                        # Add to logs
                        if bot_id not in bot_logs:
                            bot_logs[bot_id] = []
                        
                        bot_logs[bot_id].append({
                            'timestamp': datetime.now().isoformat(),
                            'level': 'info',
                            'message': line
                        })
                        
                        # Limit log size
                        if len(bot_logs[bot_id]) > 1000:
                            bot_logs[bot_id] = bot_logs[bot_id][-1000:]
                        
                        # Broadcast log
                        await sio.emit('bot_log', {
                            'bot_id': bot_id,
                            'message': line
                        })
                
                await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Error monitoring logs for bot {bot_id}: {e}")
    
    def get_all_bots(self) -> List[dict]:
        """Get all bots"""
        return list(self.bots.values())
    
    def get_stats(self) -> dict:
        """Get bot statistics"""
        total = len(self.bots)
        running = sum(1 for b in self.bots.values() if b.get('status') == 'running')
        stopped = sum(1 for b in self.bots.values() if b.get('status') == 'stopped')
        
        return {
            'total': total,
            'running': running,
            'stopped': stopped,
            'starting': total - running - stopped
        }

# Initialize bot manager
manager = BotManager()

# Metrics update loop
async def update_metrics_loop():
    """Periodically update bot metrics"""
    while True:
        try:
            for bot_id in list(manager.bots.keys()):
                await manager.update_bot_metrics(bot_id)
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Error in metrics loop: {e}")
            await asyncio.sleep(5)

# Socket.IO Event Handlers
@sio.event
async def connect(sid, environ):
    """Client connected"""
    logger.info(f"Client connected: {sid}")
    connected_clients[sid] = {
        'sid': sid,
        'connected_at': datetime.now().isoformat(),
        'type': 'web_client'
    }
    await sio.emit('welcome', {'message': 'Connected to xotiicBotHosting'}, room=sid)

@sio.event
async def disconnect(sid):
    """Client disconnected"""
    logger.info(f"Client disconnected: {sid}")
    
    # Check if it was a worker
    worker_id = None
    for wid, worker in connected_workers.items():
        if worker.get('sid') == sid:
            worker_id = wid
            break
    
    if worker_id:
        logger.info(f"Worker disconnected: {worker_id}")
        del connected_workers[worker_id]
        await sio.emit('worker_disconnected', {'worker_id': worker_id})
    
    if sid in connected_clients:
        del connected_clients[sid]

@sio.event
async def worker_register(sid, data):
    """Worker registration"""
    worker_id = data.get('id', str(uuid.uuid4()))
    logger.info(f"Worker registered: {worker_id} - {data.get('name', 'Unknown')}")
    
    data['sid'] = sid
    data['type'] = 'worker'
    data['registered_at'] = datetime.now().isoformat()
    data['last_seen'] = datetime.now().isoformat()
    data['status'] = 'online'
    
    connected_workers[worker_id] = data
    connected_clients[sid] = data
    
    await sio.emit('worker_registered', {'worker': data})
    await sio.emit('workers_list', {
        'workers': list(connected_workers.values())
    })

@sio.event
async def worker_heartbeat(sid, data):
    """Worker heartbeat"""
    worker_id = data.get('id')
    
    if 'action' in data and data['action'] == 'get_workers':
        await sio.emit('workers_list', {
            'workers': list(connected_workers.values())
        }, room=sid)
        return
    
    if worker_id in connected_workers:
        connected_workers[worker_id].update(data)
        connected_workers[worker_id]['last_seen'] = datetime.now().isoformat()
        connected_workers[worker_id]['status'] = 'online'
        
        # Broadcast to all clients
        await sio.emit('worker_heartbeat', {
            'workers': list(connected_workers.values())
        })

@sio.event
async def get_bots(sid, data):
    """Get all bots"""
    await sio.emit('bots_list', {
        'bots': manager.get_all_bots(),
        'stats': manager.get_stats()
    }, room=sid)

@sio.event
async def start_bot(sid, data):
    """Start a bot"""
    bot_id = data.get('bot_id')
    if bot_id:
        success = await manager.start_bot(bot_id)
        if success:
            await sio.emit('bot_started', {'bot_id': bot_id}, room=sid)
        else:
            await sio.emit('error', {'message': 'Failed to start bot', 'bot_id': bot_id}, room=sid)

@sio.event
async def stop_bot(sid, data):
    """Stop a bot"""
    bot_id = data.get('bot_id')
    if bot_id:
        success = await manager.stop_bot(bot_id)
        if success:
            await sio.emit('bot_stopped', {'bot_id': bot_id}, room=sid)
        else:
            await sio.emit('error', {'message': 'Failed to stop bot', 'bot_id': bot_id}, room=sid)

@sio.event
async def restart_bot(sid, data):
    """Restart a bot"""
    bot_id = data.get('bot_id')
    if bot_id:
        success = await manager.restart_bot(bot_id)
        if success:
            await sio.emit('bot_restarted', {'bot_id': bot_id}, room=sid)
        else:
            await sio.emit('error', {'message': 'Failed to restart bot', 'bot_id': bot_id}, room=sid)

@sio.event
async def delete_bot(sid, data):
    """Delete a bot"""
    bot_id = data.get('bot_id')
    if bot_id:
        success = await manager.delete_bot(bot_id)
        if success:
            await sio.emit('bot_deleted', {'bot_id': bot_id})

@sio.event
async def deploy_bot(sid, data):
    """Deploy a new bot"""
    name = data.get('name', 'New Bot')
    bot_type = data.get('type', 'python')
    token = data.get('token', '')
    code = data.get('code', '')
    
    if not token:
        await sio.emit('error', {'message': 'Bot token is required'}, room=sid)
        return
    
    try:
        bot_id = await manager.create_bot_entry(name, bot_type, token, code)
        
        # If target worker specified, assign to that worker
        target_worker = data.get('target_worker')
        if target_worker and target_worker in connected_workers:
            manager.bots[bot_id]['worker_id'] = target_worker
        
        # Broadcast new bot
        await sio.emit('bot_deployed', {
            'bot_id': bot_id,
            'bot': manager.bots[bot_id],
            'message': 'Bot deployed successfully'
        })
        
        logger.info(f"Bot deployed: {name} ({bot_id})")
        
    except Exception as e:
        logger.error(f"Error deploying bot: {e}")
        await sio.emit('error', {'message': f'Failed to deploy bot: {str(e)}'}, room=sid)

# Create Socket.IO ASGI app
socket_app = socketio.ASGIApp(
    sio,
    other_asgi_app=app,
    socketio_path='socket.io'
)

# REST API Endpoints
@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": "xotiicBotHosting API",
        "version": "2.0.0",
        "status": "online",
        "timestamp": datetime.now().isoformat(),
        "endpoints": {
            "api": "/api",
            "docs": "/docs",
            "health": "/api/health"
        }
    }

@app.get("/api/health")
async def health_check():
    """Health check"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "bots": len(manager.bots),
        "running": sum(1 for b in manager.bots.values() if b.get('status') == 'running'),
        "workers": len(connected_workers)
    }

@app.get("/api/bots")
async def get_bots_api():
    """Get all bots via REST API"""
    return {
        "bots": manager.get_all_bots(),
        "stats": manager.get_stats(),
        "timestamp": datetime.now().isoformat()
    }

@app.get("/api/workers")
async def get_workers_api():
    """Get all connected workers"""
    return {
        "workers": list(connected_workers.values()),
        "count": len(connected_workers),
        "timestamp": datetime.now().isoformat()
    }

@app.get("/api/bot/{bot_id}")
async def get_bot(bot_id: str):
    """Get specific bot"""
    if bot_id in manager.bots:
        bot = manager.bots[bot_id].copy()
        bot['logs'] = bot_logs.get(bot_id, [])[-50:]
        return bot
    raise HTTPException(status_code=404, detail="Bot not found")

@app.get("/api/bot/{bot_id}/logs")
async def get_bot_logs_api(bot_id: str, limit: int = 100):
    """Get bot logs"""
    if bot_id in bot_logs:
        logs = bot_logs[bot_id][-limit:]
        return {"logs": logs, "total": len(logs)}
    return {"logs": [], "total": 0}

@app.post("/api/bot/start/{bot_id}")
async def start_bot_api(bot_id: str):
    """Start bot via API"""
    success = await manager.start_bot(bot_id)
    if success:
        return {"message": "Bot started", "bot_id": bot_id}
    raise HTTPException(status_code=500, detail="Failed to start bot")

@app.post("/api/bot/stop/{bot_id}")
async def stop_bot_api(bot_id: str):
    """Stop bot via API"""
    success = await manager.stop_bot(bot_id)
    if success:
        return {"message": "Bot stopped", "bot_id": bot_id}
    raise HTTPException(status_code=500, detail="Failed to stop bot")

@app.post("/api/bot/restart/{bot_id}")
async def restart_bot_api(bot_id: str):
    """Restart bot via API"""
    success = await manager.restart_bot(bot_id)
    if success:
        return {"message": "Bot restarted", "bot_id": bot_id}
    raise HTTPException(status_code=500, detail="Failed to restart bot")

@app.delete("/api/bot/{bot_id}")
async def delete_bot_api(bot_id: str):
    """Delete bot via API"""
    success = await manager.delete_bot(bot_id)
    if success:
        return {"message": "Bot deleted", "bot_id": bot_id}
    raise HTTPException(status_code=404, detail="Bot not found")

@app.post("/api/upload")
async def upload_bot(
    file: UploadFile = File(...),
    name: str = Form(...),
    token: str = Form(...),
    bot_type: str = Form("python")
):
    """Upload bot files"""
    try:
        bot_id = str(uuid.uuid4())
        bot_dir = os.path.join(BOTS_DIR, bot_id)
        os.makedirs(bot_dir, exist_ok=True)
        
        # Save uploaded file
        file_path = os.path.join(bot_dir, file.filename)
        with open(file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        
        # Extract if zip
        if file.filename.endswith('.zip'):
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                zip_ref.extractall(bot_dir)
            os.remove(file_path)
        
        # Create bot entry
        bot_id = await manager.create_bot_entry(name, bot_type, token)
        
        await sio.emit('bot_deployed', {
            'bot_id': bot_id,
            'bot': manager.bots[bot_id]
        })
        
        return {
            "success": True,
            "bot_id": bot_id,
            "message": "Bot uploaded successfully"
        }
        
    except Exception as e:
        logger.error(f"Upload error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/system/stats")
async def system_stats():
    """Get system statistics"""
    cpu_percent = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    
    return {
        "bots": manager.get_stats(),
        "workers": {
            "connected": len(connected_workers),
            "total_capacity": sum(w.get('max_bots', 10) for w in connected_workers.values())
        },
        "system": {
            "cpu": round(cpu_percent, 1),
            "memory": {
                "total": round(memory.total / (1024**3), 2),
                "used": round(memory.used / (1024**3), 2),
                "percent": round(memory.percent, 1)
            }
        },
        "timestamp": datetime.now().isoformat()
    }

# Startup event
@app.on_event("startup")
async def startup_event():
    """Startup tasks"""
    logger.info("=" * 60)
    logger.info("xotiicBotHosting Server Starting")
    logger.info("=" * 60)
    logger.info(f"Upload directory: {UPLOAD_DIR}")
    logger.info(f"Bots directory: {BOTS_DIR}")
    logger.info(f"Logs directory: {LOGS_DIR}")
    
    # Start metrics update loop
    asyncio.create_task(update_metrics_loop())
    
    logger.info("Server ready!")
    logger.info("=" * 60)

# Shutdown event
@app.on_event("shutdown")
async def shutdown_event():
    """Shutdown tasks"""
    logger.info("Shutting down server...")
    
    # Stop all bots
    for bot_id in list(manager.bots.keys()):
        await manager.stop_bot(bot_id)
    
    logger.info("Server shutdown complete")

# Export the socket_app for deployment
application = socket_app

if __name__ == "__main__":
    import uvicorn
    
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")
    
    logger.info(f"Starting server on {host}:{port}")
    
    uvicorn.run(
        socket_app,
        host=host,
        port=port,
        log_level="info",
        access_log=True
    )
