from fastapi import FastAPI, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect
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
import threading
import tempfile
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
    ping_timeout=120,
    ping_interval=30
)

# Create necessary directories
UPLOAD_DIR = "uploads"
BOTS_DIR = "bots"
LOGS_DIR = "logs"
STATIC_DIR = "static"

for directory in [UPLOAD_DIR, BOTS_DIR, LOGS_DIR, STATIC_DIR]:
    Path(directory).mkdir(exist_ok=True)

# In-memory storage
connected_clients: Dict[str, dict] = {}
bots_registry: Dict[str, dict] = {}
bot_processes: Dict[str, subprocess.Popen] = {}
bot_logs: Dict[str, List[dict]] = {}
active_websockets: Set[WebSocket] = set()

# Laptops/Workers storage
connected_workers: Dict[str, dict] = {}
worker_bots: Dict[str, List[str]] = {}
bot_start_times: Dict[str, datetime] = {}

class WorkerManager:
    def __init__(self):
        self.workers = connected_workers
        self.worker_bots = worker_bots
        
    async def register_worker(self, sid: str, worker_data: dict):
        """Register a new worker/laptop"""
        worker_id = worker_data.get('id')
        if not worker_id:
            worker_id = f"worker-{uuid.uuid4().hex[:8]}"
            
        self.workers[worker_id] = {
            **worker_data,
            'sid': sid,
            'id': worker_id,
            'status': 'online',
            'last_seen': datetime.now().isoformat(),
            'connected_at': datetime.now().isoformat(),
            'running_bots': []
        }
        
        if worker_id not in self.worker_bots:
            self.worker_bots[worker_id] = []
            
        logger.info(f"Worker registered: {worker_id} - {worker_data.get('name')}")
        
        # Broadcast to all clients
        await sio.emit('worker_connected', {
            'worker': self.workers[worker_id],
            'total_workers': len(self.workers)
        })
        
        return worker_id
    
    async def update_worker_stats(self, worker_id: str, stats: dict):
        """Update worker statistics"""
        if worker_id in self.workers:
            self.workers[worker_id].update({
                'last_seen': datetime.now().isoformat(),
                'stats': stats,
                'running_bots': stats.get('running_bots', [])
            })
            
            # Broadcast update
            await sio.emit('worker_update', {
                'worker_id': worker_id,
                'worker': self.workers[worker_id]
            })
    
    async def unregister_worker(self, worker_id: str):
        """Unregister a worker"""
        if worker_id in self.workers:
            worker_name = self.workers[worker_id].get('name')
            del self.workers[worker_id]
            
            logger.info(f"Worker disconnected: {worker_id} - {worker_name}")
            
            # Broadcast to all clients
            await sio.emit('worker_disconnected', {
                'worker_id': worker_id,
                'name': worker_name,
                'total_workers': len(self.workers)
            })
    
    def assign_bot_to_worker(self, bot_id: str, worker_id: Optional[str] = None):
        """Assign a bot to a worker"""
        if worker_id and worker_id in self.workers and self.workers[worker_id].get('status') == 'online':
            # Assign to specific worker
            bots_registry[bot_id]['worker_id'] = worker_id
            self.worker_bots[worker_id].append(bot_id)
            return worker_id
        else:
            # Auto-assign to worker with least bots
            available_workers = [w_id for w_id, w in self.workers.items() 
                               if w.get('status') == 'online']
            
            if not available_workers:
                return None
                
            # Find worker with fewest bots
            target_worker = min(available_workers, 
                              key=lambda w: len(self.worker_bots.get(w, [])))
            
            bots_registry[bot_id]['worker_id'] = target_worker
            self.worker_bots[target_worker].append(bot_id)
            return target_worker
    
    def get_all_workers(self) -> List[dict]:
        """Get all workers"""
        return list(self.workers.values())
    
    def get_available_workers(self) -> List[dict]:
        """Get online workers available for bot deployment"""
        return [w for w in self.workers.values() 
                if w.get('status') == 'online']

class BotManager:
    def __init__(self):
        self.bots = bots_registry
        self.processes = bot_processes
        self.logs = bot_logs
        self.running = True
        self.worker_manager = WorkerManager()
        
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
                    bot['memory'] = round(p.memory_info().rss / (1024 * 1024), 1)
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
            'logs': [],
            'worker_id': None
        }
        
        # Initialize logs
        bot_logs[bot_id] = [{
            'timestamp': datetime.now().isoformat(),
            'level': 'info',
            'message': f'Bot "{name}" created successfully'
        }]
        
        logger.info(f"Created bot: {bot_id} - {name}")
        return bot_id
    
    async def start_bot(self, bot_id: str) -> bool:
        """Start a bot"""
        if bot_id not in self.bots:
            logger.error(f"Bot not found: {bot_id}")
            return False
        
        bot = self.bots[bot_id]
        
        # Check if bot is assigned to a worker
        if bot.get('worker_id'):
            # Send start command to worker
            worker_id = bot['worker_id']
            if worker_id in self.worker_manager.workers:
                await sio.emit('start_bot_on_worker', {
                    'bot_id': bot_id,
                    'worker_id': worker_id
                })
                return True
            else:
                logger.error(f"Worker {worker_id} not found for bot {bot_id}")
                return False
        
        # Local execution (fallback)
        if bot.get('status') == 'running':
            logger.warning(f"Bot already running: {bot_id}")
            return True
        
        try:
            bot['status'] = 'starting'
            await self.broadcast_bot_update(bot_id)
            
            bot_dir = os.path.join(BOTS_DIR, bot_id)
            os.makedirs(bot_dir, exist_ok=True)
            
            if bot['type'] == 'python':
                bot_file = os.path.join(bot_dir, 'bot.py')
                code = bot.get('code') or self._get_default_python_code(bot['name'], bot.get('token', ''))
                
                with open(bot_file, 'w') as f:
                    f.write(code)
                
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
                code = bot.get('code') or self._get_default_nodejs_code(bot['name'], bot.get('token', ''))
                
                with open(bot_file, 'w') as f:
                    f.write(code)
                
                package_json = {
                    "name": bot['name'].lower().replace(' ', '-'),
                    "version": "1.0.0",
                    "main": "bot.js",
                    "dependencies": {"discord.js": "^14.14.1"}
                }
                
                with open(os.path.join(bot_dir, 'package.json'), 'w') as f:
                    json.dump(package_json, f, indent=2)
                
                install_process = subprocess.run(
                    ['npm', 'install'],
                    cwd=bot_dir,
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                
                process = subprocess.Popen(
                    ['node', bot_file],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd=bot_dir,
                    bufsize=1,
                    universal_newlines=True
                )
            else:
                logger.error(f"Unknown bot type: {bot['type']}")
                bot['status'] = 'error'
                await self.broadcast_bot_update(bot_id)
                return False
            
            self.processes[bot_id] = process
            bot_start_times[bot_id] = datetime.now()
            bot['status'] = 'running'
            await self.broadcast_bot_update(bot_id)
            
            asyncio.create_task(self._read_process_logs(bot_id, process))
            self.add_log(bot_id, 'info', f'Bot started successfully (PID: {process.pid})')
            
            logger.info(f"Started bot: {bot_id} - {bot['name']}")
            return True
            
        except Exception as e:
            logger.error(f"Error starting bot {bot_id}: {e}")
            bot['status'] = 'error'
            await self.broadcast_bot_update(bot_id)
            self.add_log(bot_id, 'error', f'Failed to start: {str(e)}')
            return False
    
    async def stop_bot(self, bot_id: str) -> bool:
        """Stop a bot"""
        if bot_id not in self.bots:
            return False
        
        bot = self.bots[bot_id]
        
        # Check if bot is on a worker
        if bot.get('worker_id'):
            worker_id = bot['worker_id']
            if worker_id in self.worker_manager.workers:
                await sio.emit('stop_bot_on_worker', {
                    'bot_id': bot_id,
                    'worker_id': worker_id
                })
                return True
        
        # Local execution
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
                
                self.add_log(bot_id, 'info', 'Bot stopped')
            except Exception as e:
                logger.error(f"Error stopping bot {bot_id}: {e}")
        
        bot['status'] = 'stopped'
        bot['cpu'] = 0
        bot['memory'] = 0
        bot['uptime'] = '0s'
        await self.broadcast_bot_update(bot_id)
        
        logger.info(f"Stopped bot: {bot_id}")
        return True
    
    async def restart_bot(self, bot_id: str) -> bool:
        """Restart a bot"""
        self.add_log(bot_id, 'info', 'Restarting bot...')
        await self.stop_bot(bot_id)
        await asyncio.sleep(2)
        return await self.start_bot(bot_id)
    
    async def delete_bot(self, bot_id: str) -> bool:
        """Delete a bot"""
        await self.stop_bot(bot_id)
        
        bot_dir = os.path.join(BOTS_DIR, bot_id)
        if os.path.exists(bot_dir):
            shutil.rmtree(bot_dir)
        
        if bot_id in self.bots:
            # Remove from worker if assigned
            worker_id = self.bots[bot_id].get('worker_id')
            if worker_id and worker_id in worker_bots and bot_id in worker_bots[worker_id]:
                worker_bots[worker_id].remove(bot_id)
            
            del self.bots[bot_id]
        
        if bot_id in bot_logs:
            del bot_logs[bot_id]
        
        logger.info(f"Deleted bot: {bot_id}")
        return True
    
    def add_log(self, bot_id: str, level: str, message: str):
        """Add a log entry for a bot"""
        if bot_id not in bot_logs:
            bot_logs[bot_id] = []
        
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'level': level,
            'message': message
        }
        
        bot_logs[bot_id].append(log_entry)
        
        if len(bot_logs[bot_id]) > 200:
            bot_logs[bot_id] = bot_logs[bot_id][-200:]
        
        asyncio.create_task(sio.emit('bot_log', {
            'bot_id': bot_id,
            'log': log_entry
        }))
    
    async def _read_process_logs(self, bot_id: str, process: subprocess.Popen):
        """Read logs from bot process"""
        try:
            while True:
                if process.poll() is not None:
                    if bot_id in self.bots:
                        self.bots[bot_id]['status'] = 'stopped'
                        await self.broadcast_bot_update(bot_id)
                    break
                
                line = process.stdout.readline()
                if line:
                    self.add_log(bot_id, 'info', line.strip())
                else:
                    await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Error reading logs for {bot_id}: {e}")
    
    async def broadcast_bot_update(self, bot_id: str):
        """Broadcast bot update to all clients"""
        if bot_id in self.bots:
            await self.update_bot_metrics(bot_id)
            bot = self.bots[bot_id]
            
            await sio.emit('bot_update', {
                'bot': bot,
                'timestamp': datetime.now().isoformat()
            })
    
    def get_all_bots(self) -> List[dict]:
        """Get all bots"""
        return list(self.bots.values())
    
    def get_stats(self) -> dict:
        """Get overall stats"""
        total = len(self.bots)
        running = sum(1 for b in self.bots.values() if b.get('status') == 'running')
        
        total_cpu = sum(b.get('cpu', 0) for b in self.bots.values())
        total_memory = sum(b.get('memory', 0) for b in self.bots.values())
        
        return {
            'total_bots': total,
            'running_bots': running,
            'stopped_bots': total - running,
            'total_cpu': round(total_cpu, 1),
            'total_memory': round(total_memory, 1)
        }
    
    def _get_default_python_code(self, bot_name: str, token: str) -> str:
        return f"""
import discord
from discord.ext import commands
import os

TOKEN = '{token}'

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'{{bot.user}} is now online!')
    print(f'Hosted on xotiicBotHosting')

@bot.command()
async def ping(ctx):
    await ctx.send(f'Pong! {{round(bot.latency * 1000)}}ms')

@bot.command()
async def hello(ctx):
    await ctx.send(f'Hello {{ctx.author.mention}}!')

if __name__ == '__main__':
    bot.run(TOKEN)
"""
    
    def _get_default_nodejs_code(self, bot_name: str, token: str) -> str:
        return f"""
const {{ Client, GatewayIntentBits }} = require('discord.js');

const client = new Client({{
    intents: [
        GatewayIntentBits.Guilds,
        GatewayIntentBits.GuildMessages,
        GatewayIntentBits.MessageContent
    ]
}});

client.once('ready', () => {{
    console.log(`${{client.user.tag}} is now online!`);
    console.log(`Hosted on xotiicBotHosting`);
}});

client.on('messageCreate', async message => {{
    if (message.content === '!ping') {{
        message.reply(`Pong! ${{client.ws.ping}}ms`);
    }}
    if (message.content === '!hello') {{
        message.reply(`Hello ${{message.author}}!`);
    }}
}});

client.login('{token}');
"""

# Initialize managers
manager = BotManager()
worker_manager = WorkerManager()

# Background task to update metrics
async def update_metrics_loop():
    """Periodically update bot metrics"""
    while True:
        try:
            for bot_id in list(manager.bots.keys()):
                if manager.bots[bot_id].get('status') == 'running':
                    await manager.update_bot_metrics(bot_id)
                    await manager.broadcast_bot_update(bot_id)
            
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Error in metrics loop: {e}")
            await asyncio.sleep(5)

# Socket.IO events
@sio.event
async def connect(sid, environ):
    """Handle client connection"""
    logger.info(f"Client connected: {sid}")
    
    await sio.emit('welcome', {
        'message': 'Connected to xotiicBotHosting Server',
        'server_time': datetime.now().isoformat(),
        'sid': sid
    }, room=sid)
    
    # Send all bots
    await sio.emit('bots_list', {
        'bots': manager.get_all_bots(),
        'stats': manager.get_stats()
    }, room=sid)
    
    # Send workers list
    await sio.emit('laptops_list', {
        'laptops': worker_manager.get_all_workers(),
        'total': len(worker_manager.workers)
    }, room=sid)

@sio.event
async def disconnect(sid):
    """Handle client disconnect"""
    logger.info(f"Client disconnected: {sid}")
    
    # Check if this was a worker
    for worker_id, worker in worker_manager.workers.items():
        if worker.get('sid') == sid:
            await worker_manager.unregister_worker(worker_id)
            break

# Worker registration
@sio.event
async def worker_register(sid, data):
    """Handle worker registration"""
    worker_id = await worker_manager.register_worker(sid, data)
    if worker_id:
        await sio.emit('welcome_worker', {
            'message': f'Worker {data.get("name")} registered successfully',
            'worker_id': worker_id
        }, room=sid)

@sio.event
async def worker_heartbeat(sid, data):
    """Handle worker heartbeat"""
    worker_id = data.get('id')
    if worker_id:
        await worker_manager.update_worker_stats(worker_id, data)

# Bot management events
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
async def deploy_bot(sid, data):
    """Deploy a new bot"""
    name = data.get('name', 'New Bot')
    bot_type = data.get('type', 'python')
    token = data.get('token', '')
    code = data.get('code', '')
    config = data.get('config', '')
    target_worker = data.get('target_worker', None)
    
    if not token:
        await sio.emit('error', {'message': 'Bot token is required'}, room=sid)
        return
    
    try:
        bot_id = await manager.create_bot_entry(name, bot_type, token, code)
        
        # Save config if provided
        if config:
            bot_dir = os.path.join(BOTS_DIR, bot_id)
            os.makedirs(bot_dir, exist_ok=True)
            config_path = os.path.join(bot_dir, 'config.txt')
            with open(config_path, 'w') as f:
                f.write(config)
        
        # Assign to worker if available
        assigned_worker = worker_manager.assign_bot_to_worker(bot_id, target_worker)
        
        if assigned_worker:
            manager.bots[bot_id]['worker_id'] = assigned_worker
            manager.bots[bot_id]['status'] = 'deployed'
            
            # Send to worker
            await sio.emit('deploy_bot_to_worker', {
                'bot_id': bot_id,
                'name': name,
                'type': bot_type,
                'token': token,
                'code': code,
                'config': config,
                'target_worker': assigned_worker
            })
            
            message = f'Bot deployed to worker {assigned_worker}'
        else:
            manager.bots[bot_id]['status'] = 'deployed'
            message = 'Bot deployed locally (no workers available)'
        
        await manager.broadcast_bot_update(bot_id)
        
        await sio.emit('bot_deployed', {
            'bot_id': bot_id,
            'bot': manager.bots[bot_id],
            'message': message
        })
        
    except Exception as e:
        logger.error(f"Error deploying bot: {e}")
        await sio.emit('error', {'message': f'Failed to deploy bot: {str(e)}'}, room=sid)

@sio.event
async def get_bots(sid, data):
    """Get all bots"""
    await sio.emit('bots_list', {
        'bots': manager.get_all_bots(),
        'stats': manager.get_stats()
    }, room=sid)

@sio.event
async def get_laptops(sid, data):
    """Get all laptops/workers"""
    await sio.emit('laptops_list', {
        'laptops': worker_manager.get_all_workers(),
        'total': len(worker_manager.workers)
    }, room=sid)

# Worker bot events
@sio.event
async def bot_deployed_on_worker(sid, data):
    """Handle bot deployed on worker"""
    bot_id = data.get('bot_id')
    worker_id = data.get('worker_id')
    
    if bot_id in manager.bots:
        manager.bots[bot_id]['status'] = 'deployed'
        manager.bots[bot_id]['worker_id'] = worker_id
        await manager.broadcast_bot_update(bot_id)

@sio.event
async def bot_started_on_worker(sid, data):
    """Handle bot started on worker"""
    bot_id = data.get('bot_id')
    worker_id = data.get('worker_id')
    
    if bot_id in manager.bots:
        manager.bots[bot_id]['status'] = 'running'
        manager.bots[bot_id]['worker_id'] = worker_id
        await manager.broadcast_bot_update(bot_id)
        
        await sio.emit('bot_started', {
            'bot_id': bot_id,
            'worker_id': worker_id
        })

@sio.event
async def bot_stopped_on_worker(sid, data):
    """Handle bot stopped on worker"""
    bot_id = data.get('bot_id')
    worker_id = data.get('worker_id')
    
    if bot_id in manager.bots:
        manager.bots[bot_id]['status'] = 'stopped'
        await manager.broadcast_bot_update(bot_id)
        
        await sio.emit('bot_stopped', {
            'bot_id': bot_id,
            'worker_id': worker_id
        })

# Create Socket.IO ASGI app
socket_app = socketio.ASGIApp(
    sio,
    other_asgi_app=app,
    socketio_path='socket.io'
)

# REST API Endpoints
@app.get("/")
async def root():
    return {
        "name": "xotiicBotHosting API",
        "version": "2.0.0",
        "status": "online",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "bots": len(manager.bots),
        "workers": len(worker_manager.workers)
    }

@app.get("/api/bots")
async def get_bots_api():
    return {
        "bots": manager.get_all_bots(),
        "stats": manager.get_stats(),
        "timestamp": datetime.now().isoformat()
    }

@app.get("/api/bot/{bot_id}")
async def get_bot(bot_id: str):
    if bot_id in manager.bots:
        bot = manager.bots[bot_id].copy()
        bot['logs'] = bot_logs.get(bot_id, [])[-50:]
        return bot
    raise HTTPException(status_code=404, detail="Bot not found")

@app.post("/api/bot/start/{bot_id}")
async def start_bot_api(bot_id: str):
    success = await manager.start_bot(bot_id)
    if success:
        return {"message": "Bot started", "bot_id": bot_id}
    raise HTTPException(status_code=500, detail="Failed to start bot")

@app.post("/api/bot/stop/{bot_id}")
async def stop_bot_api(bot_id: str):
    success = await manager.stop_bot(bot_id)
    if success:
        return {"message": "Bot stopped", "bot_id": bot_id}
    raise HTTPException(status_code=500, detail="Failed to stop bot")

@app.delete("/api/bot/{bot_id}")
async def delete_bot_api(bot_id: str):
    success = await manager.delete_bot(bot_id)
    if success:
        return {"message": "Bot deleted", "bot_id": bot_id}
    raise HTTPException(status_code=404, detail="Bot not found")

@app.get("/api/laptops")
async def get_laptops_api():
    """Get all connected laptops/workers"""
    return {
        "laptops": worker_manager.get_all_workers(),
        "total": len(worker_manager.workers),
        "online": len(worker_manager.get_available_workers()),
        "timestamp": datetime.now().isoformat()
    }

@app.get("/api/worker/{worker_id}")
async def get_worker(worker_id: str):
    """Get specific worker details"""
    if worker_id in worker_manager.workers:
        worker = worker_manager.workers[worker_id].copy()
        worker['bots'] = worker_bots.get(worker_id, [])
        return worker
    raise HTTPException(status_code=404, detail="Worker not found")

@app.get("/api/system/stats")
async def system_stats():
    cpu_percent = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    
    return {
        "bots": manager.get_stats(),
        "workers": {
            "total": len(worker_manager.workers),
            "online": len(worker_manager.get_available_workers())
        },
        "system": {
            "cpu": round(cpu_percent, 1),
            "memory": round(memory.percent, 1)
        },
        "timestamp": datetime.now().isoformat()
    }

@app.post("/api/upload")
async def upload_bot(
    file: UploadFile = File(...),
    name: str = Form(...),
    token: str = Form(...),
    bot_type: str = Form("python"),
    config_file: UploadFile = File(None)
):
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
        
        # Assign to available worker
        assigned_worker = worker_manager.assign_bot_to_worker(bot_id)
        if assigned_worker:
            manager.bots[bot_id]['worker_id'] = assigned_worker
            manager.bots[bot_id]['status'] = 'deployed'
        
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

# Startup event
@app.on_event("startup")
async def startup_event():
    logger.info("=" * 60)
    logger.info("xotiicBotHosting Server Starting")
    logger.info("=" * 60)
    logger.info(f"Upload directory: {UPLOAD_DIR}")
    logger.info(f"Bots directory: {BOTS_DIR}")
    logger.info(f"Workers system: Ready for connections")
    
    asyncio.create_task(update_metrics_loop())
    
    logger.info("Server ready!")
    logger.info("=" * 60)

# Shutdown event
@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down server...")
    
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
        log_level="info"
    )
