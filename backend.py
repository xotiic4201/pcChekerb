import os
import logging
import secrets
import string
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Header, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from dotenv import load_dotenv
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import threading

# Load environment variables
load_dotenv()

# ==================== CONFIGURATION ====================
TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '0'))
GUILD_ID = int(os.getenv('GUILD_ID', '0'))  # Your Discord server ID
API_KEY = os.getenv('API_KEY', 'rnd_o2SUQpg4Ln3EsJSJsOYOeCHnLnId')
RENDER_URL = os.getenv('RENDER_URL', 'https://r6x-cyberscan-api.onrender.com')

# File paths
EXE_FILENAME = "R6XScan.exe"
EXE_PATH = os.path.join(os.path.dirname(__file__), EXE_FILENAME)

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("r6x-backend")

# Check if token exists
if not TOKEN:
    logger.error("❌ DISCORD_TOKEN not found in environment variables!")
    raise ValueError("DISCORD_TOKEN is required")

if not CHANNEL_ID:
    logger.error("❌ CHANNEL_ID not found in environment variables!")
    raise ValueError("CHANNEL_ID is required")

# ==================== FASTAPI APP ====================
app = FastAPI(
    title="R6X CyberScan API", 
    version="1.0.0",
    description="API for R6X CyberScan Discord Bot with Slash Commands"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== PYDANTIC MODELS ====================
class StartScanRequest(BaseModel):
    user_id: str

class StartScanResponse(BaseModel):
    scan_id: str
    message: str

class GenerateKeyRequest(BaseModel):
    user_id: str
    duration_days: Optional[int] = 30

class GenerateKeyResponse(BaseModel):
    key: str
    user_id: str
    expires_at: str
    message: str

class ScanCompleteRequest(BaseModel):
    scan_id: str
    user_id: str
    files_scanned: int
    suspicious_count: int
    duration: float
    logitech: Optional[Dict[str, Any]] = None
    log_file_url: Optional[str] = None

class ScanResponse(BaseModel):
    status: str
    message: str
    scan_id: Optional[str] = None

class ValidateKeyResponse(BaseModel):
    valid: bool
    user_id: str
    available_keys: Optional[int] = None
    message: str

# ==================== KEY MANAGEMENT ====================
class KeyManager:
    def __init__(self):
        self.keys = {}  # In-memory storage (use database in production)
        self.user_keys = {}
        self.load_keys()
    
    def load_keys(self):
        """Load keys from file if exists"""
        keys_file = os.path.join(os.path.dirname(__file__), 'keys.json')
        if os.path.exists(keys_file):
            try:
                with open(keys_file, 'r') as f:
                    data = json.load(f)
                    self.keys = data.get('keys', {})
                    self.user_keys = data.get('user_keys', {})
                    logger.info(f"✅ Loaded {len(self.keys)} keys from file")
            except Exception as e:
                logger.error(f"Failed to load keys: {e}")
    
    def save_keys(self):
        """Save keys to file"""
        keys_file = os.path.join(os.path.dirname(__file__), 'keys.json')
        try:
            with open(keys_file, 'w') as f:
                json.dump({
                    'keys': self.keys,
                    'user_keys': self.user_keys
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save keys: {e}")
    
    def generate_key(self, user_id: str, duration_days: int = 30) -> str:
        """Generate a unique key for a user"""
        alphabet = string.ascii_uppercase + string.digits
        part1 = ''.join(secrets.choice(alphabet) for _ in range(5))
        part2 = ''.join(secrets.choice(alphabet) for _ in range(5))
        part3 = ''.join(secrets.choice(alphabet) for _ in range(5))
        key = f"R6X-{part1}-{part2}-{part3}"
        
        expires_at = (datetime.now() + timedelta(days=duration_days)).timestamp()
        
        self.keys[key] = {
            'user_id': user_id,
            'expires_at': expires_at,
            'used': False,
            'created_at': datetime.now().isoformat(),
            'duration_days': duration_days
        }
        
        if user_id not in self.user_keys:
            self.user_keys[user_id] = []
        self.user_keys[user_id].append(key)
        
        self.save_keys()
        logger.info(f"✅ Generated key {key} for user {user_id}")
        return key
    
    def validate_key(self, key: str, user_id: str) -> tuple:
        """Validate if a key is valid for a user"""
        if key not in self.keys:
            return False, "Key not found"
        
        key_data = self.keys[key]
        
        if key_data['user_id'] != user_id:
            return False, f"Key belongs to user {key_data['user_id']}"
        
        if key_data['used']:
            return False, "Key has already been used"
        
        if datetime.now().timestamp() > key_data['expires_at']:
            return False, "Key has expired"
        
        return True, "Key is valid"
    
    def validate_user(self, user_id: str) -> tuple:
        """Check if user has any valid key"""
        if user_id not in self.user_keys:
            return False, "No keys found for this user"
        
        valid_keys = []
        for key in self.user_keys[user_id]:
            if key in self.keys:
                key_data = self.keys[key]
                if not key_data['used'] and datetime.now().timestamp() <= key_data['expires_at']:
                    valid_keys.append(key)
        
        if valid_keys:
            return True, f"User has {len(valid_keys)} valid key(s)"
        else:
            return False, "No valid keys found (all used or expired)"
    
    def mark_key_used(self, key: str):
        """Mark a key as used"""
        if key in self.keys:
            self.keys[key]['used'] = True
            self.keys[key]['used_at'] = datetime.now().isoformat()
            self.save_keys()
            logger.info(f"✅ Key {key} marked as used")
            return True
        return False
    
    def use_one_key(self, user_id: str) -> Optional[str]:
        """Use one valid key for a user (returns the key used)"""
        if user_id not in self.user_keys:
            return None
        
        for key in self.user_keys[user_id]:
            if key in self.keys:
                key_data = self.keys[key]
                if not key_data['used'] and datetime.now().timestamp() <= key_data['expires_at']:
                    self.mark_key_used(key)
                    return key
        
        return None
    
    def get_user_keys(self, user_id: str) -> List[Dict]:
        """Get all keys for a user"""
        keys = self.user_keys.get(user_id, [])
        result = []
        for key in keys:
            if key in self.keys:
                key_data = self.keys[key].copy()
                key_data['key'] = key
                key_data['expires_at_date'] = datetime.fromtimestamp(key_data['expires_at']).isoformat()
                key_data['valid'] = not key_data['used'] and datetime.now().timestamp() <= key_data['expires_at']
                result.append(key_data)
        return result
    
    def get_stats(self) -> Dict:
        """Get key statistics"""
        total_keys = len(self.keys)
        used_keys = sum(1 for k in self.keys.values() if k['used'])
        valid_keys = sum(1 for k in self.keys.values() 
                        if not k['used'] and datetime.now().timestamp() <= k['expires_at'])
        
        return {
            'total_keys': total_keys,
            'used_keys': used_keys,
            'valid_keys': valid_keys,
            'unique_users': len(self.user_keys)
        }

# Initialize key manager
key_manager = KeyManager()

# ==================== DATA STORAGE ====================
active_scans = {}
scan_history = []
bot_start_time = datetime.now()

# ==================== DISCORD BOT ====================
class R6XBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix='!', intents=intents)
        self.key_manager = key_manager
        self.channel_id = CHANNEL_ID
    
    async def setup_hook(self):
        await self.add_cog(KeyCommands(self))
        await self.tree.sync()
        logger.info("✅ Slash commands synced")
    
    async def on_ready(self):
        logger.info(f"✅ Bot connected as {self.user.name}")
        logger.info(f"✅ Bot ID: {self.user.id}")
        
        # Set status
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="for /commands"
            )
        )
        
        # Send startup message
        channel = self.get_channel(self.channel_id)
        if channel:
            embed = discord.Embed(
                title="🤖 R6X CyberScan Bot Online",
                description="Bot is ready to accept commands!",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            embed.add_field(name="Commands", value="/generate_key\n/list_keys\n/validate\n/stats\n/help", inline=False)
            await channel.send(embed=embed)

class KeyCommands(commands.Cog):
    def __init__(self, bot: R6XBot):
        self.bot = bot
    
    @app_commands.command(name="generate_key", description="Generate a new license key for a user")
    @app_commands.describe(
        user_id="Discord User ID to generate key for",
        duration_days="Number of days the key is valid for (default: 30)"
    )
    async def generate_key(
        self, 
        interaction: discord.Interaction, 
        user_id: str,
        duration_days: int = 30
    ):
        await interaction.response.defer()
        
        try:
            key = self.bot.key_manager.generate_key(user_id, duration_days)
            key_data = self.bot.key_manager.get_user_keys(user_id)
            key_info = next((k for k in key_data if k['key'] == key), None)
            
            embed = discord.Embed(
                title="✅ Key Generated Successfully",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            
            embed.add_field(
                name="🔑 Key",
                value=f"```\n{key}\n```",
                inline=False
            )
            
            embed.add_field(
                name="👤 User ID",
                value=f"```\n{user_id}\n```",
                inline=True
            )
            
            embed.add_field(
                name="⏰ Expires",
                value=f"```\n{key_info['expires_at_date']}\n```",
                inline=True
            )
            
            embed.add_field(
                name="📅 Duration",
                value=f"```\n{duration_days} days\n```",
                inline=True
            )
            
            embed.set_footer(text=f"Generated by {interaction.user.name}")
            
            await interaction.followup.send(embed=embed)
            
            # Also send to log channel
            log_channel = self.bot.get_channel(self.bot.channel_id)
            if log_channel and log_channel.id != interaction.channel_id:
                log_embed = discord.Embed(
                    title="🔑 Key Generated",
                    description=f"User: <@{user_id}>\nGenerated by: {interaction.user.mention}",
                    color=discord.Color.blue()
                )
                log_embed.add_field(name="Key", value=f"`{key}`", inline=False)
                await log_channel.send(embed=log_embed)
                
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)
    
    @app_commands.command(name="list_keys", description="List all keys for a user")
    @app_commands.describe(user_id="Discord User ID to list keys for")
    async def list_keys(self, interaction: discord.Interaction, user_id: str):
        await interaction.response.defer()
        
        keys = self.bot.key_manager.get_user_keys(user_id)
        
        if not keys:
            await interaction.followup.send(f"ℹ️ No keys found for user {user_id}", ephemeral=True)
            return
        
        embed = discord.Embed(
            title=f"🔑 Keys for User {user_id}",
            description=f"Total Keys: {len(keys)}",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        valid_count = sum(1 for k in keys if k['valid'])
        embed.add_field(name="✅ Valid", value=str(valid_count), inline=True)
        embed.add_field(name="❌ Used/Expired", value=str(len(keys) - valid_count), inline=True)
        
        for i, key_info in enumerate(keys[:5], 1):
            status = "✅ VALID" if key_info['valid'] else "❌ USED/EXPIRED"
            expires = key_info['expires_at_date'][:10]
            
            embed.add_field(
                name=f"Key {i}",
                value=f"`{key_info['key']}`\nStatus: {status}\nExpires: {expires}",
                inline=False
            )
        
        if len(keys) > 5:
            embed.set_footer(text=f"Showing 5 of {len(keys)} keys")
        
        await interaction.followup.send(embed=embed)
    
    @app_commands.command(name="validate", description="Check if a user has a valid key")
    @app_commands.describe(user_id="Discord User ID to validate")
    async def validate(self, interaction: discord.Interaction, user_id: str):
        await interaction.response.defer()
        
        valid, message = self.bot.key_manager.validate_user(user_id)
        
        if valid:
            embed = discord.Embed(
                title="✅ Valid Key Found",
                description=message,
                color=discord.Color.green()
            )
        else:
            embed = discord.Embed(
                title="❌ No Valid Key",
                description=message,
                color=discord.Color.red()
            )
        
        await interaction.followup.send(embed=embed)
    
    @app_commands.command(name="stats", description="Get bot statistics")
    async def stats(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        stats = self.bot.key_manager.get_stats()
        
        embed = discord.Embed(
            title="📊 Bot Statistics",
            color=discord.Color.gold(),
            timestamp=datetime.now()
        )
        
        embed.add_field(name="🔑 Total Keys", value=f"```\n{stats['total_keys']}\n```", inline=True)
        embed.add_field(name="✅ Valid Keys", value=f"```\n{stats['valid_keys']}\n```", inline=True)
        embed.add_field(name="❌ Used Keys", value=f"```\n{stats['used_keys']}\n```", inline=True)
        embed.add_field(name="👥 Unique Users", value=f"```\n{stats['unique_users']}\n```", inline=True)
        embed.add_field(name="🟢 Active Scans", value=f"```\n{len(active_scans)}\n```", inline=True)
        embed.add_field(name="📁 Total Scans", value=f"```\n{len(scan_history)}\n```", inline=True)
        
        await interaction.followup.send(embed=embed)
    
    @app_commands.command(name="help", description="Show available commands")
    async def help_command(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🤖 R6X CyberScan Bot Commands",
            description="Available slash commands:",
            color=discord.Color.blue()
        )
        
        commands = [
            ("/generate_key [user_id] [days]", "Generate a new license key"),
            ("/list_keys [user_id]", "List all keys for a user"),
            ("/validate [user_id]", "Check if user has valid key"),
            ("/stats", "Show bot statistics"),
            ("/help", "Show this help message")
        ]
        
        for cmd, desc in commands:
            embed.add_field(name=cmd, value=desc, inline=False)
        
        await interaction.response.send_message(embed=embed)

# Start Discord bot in background thread
def run_discord_bot():
    bot = R6XBot()
    asyncio.run(bot.start(TOKEN))

discord_thread = threading.Thread(target=run_discord_bot, daemon=True)
discord_thread.start()
logger.info("✅ Discord bot thread started")

# ==================== FASTAPI ROUTES ====================

@app.get("/")
async def root():
    return {
        "name": "R6X CyberScan API",
        "version": "1.0.0",
        "status": "online",
        "bot_status": "running",
        "key_system": "active",
        "endpoints": {
            "/health": "Health check",
            "/api/validate-key": "Validate if user has a key",
            "/api/start-scan": "Start a new scan (uses one key)",
            "/api/scan/complete": "Mark scan as complete",
            "/api/user/keys/{user_id}": "Get user's keys",
            "/api/stats": "Get statistics"
        }
    }

@app.post("/api/validate-key", response_model=ValidateKeyResponse)
async def validate_key(request: StartScanRequest, x_api_key: Optional[str] = Header(None)):
    """Validate if a user has a valid key"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    user_id = request.user_id
    valid, message = key_manager.validate_user(user_id)
    
    if valid:
        keys = key_manager.get_user_keys(user_id)
        valid_keys = [k for k in keys if k['valid']]
        return ValidateKeyResponse(
            valid=True,
            user_id=user_id,
            available_keys=len(valid_keys),
            message=message
        )
    else:
        return ValidateKeyResponse(
            valid=False,
            user_id=user_id,
            message=message
        )

@app.post("/api/start-scan", response_model=StartScanResponse)
async def start_scan(request: StartScanRequest, x_api_key: Optional[str] = Header(None)):
    """Start a new scan (uses one valid key)"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    user_id = request.user_id
    
    # Use one valid key
    used_key = key_manager.use_one_key(user_id)
    
    if not used_key:
        raise HTTPException(
            status_code=403, 
            detail="No valid key found. Generate one in Discord with /generate_key"
        )
    
    # Generate scan ID
    scan_id = f"R6X-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{user_id[-8:]}"
    
    # Store scan session
    active_scans[scan_id] = {
        'user_id': user_id,
        'start_time': datetime.now(),
        'status': 'pending',
        'key_used': used_key
    }
    
    logger.info(f"✅ Scan started: {scan_id} for user {user_id} (key: {used_key})")
    
    return StartScanResponse(
        scan_id=scan_id,
        message=f"Scan started successfully using key {used_key}"
    )

@app.post("/api/scan/complete")
async def scan_complete(request: ScanCompleteRequest, x_api_key: Optional[str] = Header(None)):
    """Mark scan as complete and store results"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    scan_id = request.scan_id
    user_id = request.user_id
    
    if scan_id not in active_scans:
        raise HTTPException(status_code=404, detail="Invalid or expired scan ID")
    
    if active_scans[scan_id]['user_id'] != user_id:
        raise HTTPException(status_code=403, detail="User mismatch")
    
    # Update scan status
    active_scans[scan_id]['status'] = 'completed'
    active_scans[scan_id]['completed_time'] = datetime.now()
    active_scans[scan_id]['data'] = request.dict()
    
    # Add to history
    scan_history.append({
        'scan_id': scan_id,
        'user_id': user_id,
        'completed_time': datetime.now().isoformat(),
        'files_scanned': request.files_scanned,
        'suspicious_count': request.suspicious_count,
        'duration': request.duration,
        'key_used': active_scans[scan_id].get('key_used'),
        'logitech': request.logitech
    })
    
    logger.info(f"✅ Scan completed: {scan_id}")
    
    return ScanResponse(
        status='success',
        message='Scan marked as complete',
        scan_id=scan_id
    )

@app.get("/api/user/keys/{user_id}")
async def get_user_keys(user_id: str, x_api_key: Optional[str] = Header(None)):
    """Get all keys for a specific user"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    keys = key_manager.get_user_keys(user_id)
    return {
        'user_id': user_id,
        'total_keys': len(keys),
        'keys': keys
    }

@app.get("/api/stats")
async def get_stats(x_api_key: Optional[str] = Header(None)):
    """Get overall statistics"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    total_scans = len(scan_history)
    total_files = sum(s.get('files_scanned', 0) for s in scan_history)
    total_suspicious = sum(s.get('suspicious_count', 0) for s in scan_history)
    
    avg_duration = 0
    if total_scans > 0:
        avg_duration = sum(s.get('duration', 0) for s in scan_history) / total_scans
    
    key_stats = key_manager.get_stats()
    
    return {
        'total_scans': total_scans,
        'total_files_scanned': total_files,
        'total_suspicious_files': total_suspicious,
        'average_duration': avg_duration,
        'active_scans': len(active_scans),
        'key_stats': key_stats
    }

@app.get("/health")
async def health():
    """Health check endpoint"""
    key_stats = key_manager.get_stats()
    
    return {
        'status': 'healthy',
        'bot_status': 'running',
        'key_system': key_stats,
        'active_scans': len(active_scans),
        'total_scans_completed': len(scan_history),
        'uptime': str(datetime.now() - bot_start_time).split('.')[0]
    }

if __name__ == "__main__":
    port = int(os.getenv('PORT', 5000))
    logger.info(f"Starting R6X CyberScan API on port {port}")
    logger.info(f"Discord bot is running in background thread")
    logger.info(f"Key generation system: ACTIVE")
    
    uvicorn.run(app, host="0.0.0.0", port=port)
