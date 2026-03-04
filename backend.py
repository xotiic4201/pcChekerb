# app.py - R6X CYBERSCAN Backend with Supabase & Discord Bot Hosting
# Deploy this on Render.com

import os
import jwt
import bcrypt
import datetime
import asyncio
import uuid
import json
import threading
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Depends, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from supabase import create_client, Client
import logging
from datetime import datetime, timedelta
import secrets
import traceback

# Discord imports
import discord
from discord.ext import commands
import asyncio
import threading

# ========== CONFIGURATION ==========
class Config:
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
    SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
    SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_urlsafe(32))
    ALGORITHM = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES = 30
    REFRESH_TOKEN_EXPIRE_DAYS = 7
    ADMIN_IDS = os.getenv("ADMIN_IDS", "1151697240025464852,1302203907782606880").split(",")
    
config = Config()

# ========== LOGGING ==========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("r6x-backend")

# ========== SUPABASE ==========
supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
service_supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY)

# ========== PYDANTIC MODELS ==========
class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8)

class UserLogin(BaseModel):
    username: str
    password: str

class ScanResult(BaseModel):
    username: str
    computer: str
    files_scanned: int
    threats_found: int
    suspicious_files: List[Dict]
    r6_accounts: List[Dict]
    steam_accounts: List[Dict]
    windows_install_date: Optional[str] = None
    antivirus_status: Optional[str] = None
    prefetch_files: Optional[List] = None
    logitech_scripts: Optional[List] = None
    scan_time: str

class BotConnect(BaseModel):
    user_id: str
    bot_token: str
    channel_id: str

# ========== DISCORD BOT MANAGER ==========
class DiscordBotHost:
    def __init__(self):
        self.bots = {}  # user_id -> bot instance
        self.bot_tasks = {}  # user_id -> task
        self.loops = {}  # user_id -> event loop
        self.threads = {}  # user_id -> thread
        logger.info("✅ Discord Bot Host initialized")
        
    def start_bot_for_user(self, user_id: str, bot_token: str, channel_id: str):
        """Start a Discord bot for a specific user in its own thread"""
        
        def run_bot():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.loops[user_id] = loop
            
            try:
                loop.run_until_complete(self._run_bot(user_id, bot_token, channel_id))
            except Exception as e:
                logger.error(f"Bot error for user {user_id}: {e}")
            finally:
                loop.close()
                if user_id in self.loops:
                    del self.loops[user_id]
        
        thread = threading.Thread(target=run_bot, daemon=True)
        thread.start()
        self.threads[user_id] = thread
        
    async def _run_bot(self, user_id: str, bot_token: str, channel_id: str):
        """Run the actual bot"""
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        
        bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)
        self.bots[user_id] = bot
        
        # Get username from database
        result = service_supabase.table("users").select("username").eq("user_id", user_id).execute()
        username = result.data[0]["username"] if result.data else "Unknown"
        
        @bot.event
        async def on_ready():
            logger.info(f"✅ Bot for {username} connected as {bot.user}")
            
            # Store bot info in database
            service_supabase.table("discord_bots").upsert({
                "user_id": user_id,
                "bot_token_preview": bot_token[:10] + "...",
                "channel_id": channel_id,
                "bot_name": str(bot.user),
                "status": "connected",
                "connected_at": datetime.utcnow().isoformat()
            }).execute()
            
            # Send welcome message
            channel = bot.get_channel(int(channel_id))
            if not channel:
                for guild in bot.guilds:
                    channel = guild.get_channel(int(channel_id))
                    if channel:
                        break
            
            if channel:
                embed = discord.Embed(
                    title="🤖 R6X Bot Connected",
                    description=f"Scanner activated for **{username}**",
                    color=0x00FF9D,
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Status", value="🟢 Online", inline=True)
                embed.add_field(name="Commands", value="!scan, !status, !help", inline=True)
                embed.set_footer(text="R6X CyberScan")
                await channel.send(embed=embed)
        
        @bot.event
        async def on_command_error(ctx, error):
            if isinstance(error, commands.CommandNotFound):
                return
            await ctx.send(f"❌ Error: {str(error)}")
        
        @bot.command(name='scan')
        async def scan_command(ctx):
            """Get latest scan results"""
            if str(ctx.channel.id) != channel_id:
                return
            
            # Get latest scan from database
            result = service_supabase.table("scans")\
                .select("*")\
                .eq("user_id", user_id)\
                .order("created_at", desc=True)\
                .limit(1)\
                .execute()
            
            if result.data and len(result.data) > 0:
                scan = result.data[0]
                color = 0x00FF9D if scan['threats_found'] == 0 else 0xFF003C
                
                embed = discord.Embed(
                    title="📊 Latest Scan Results",
                    description=f"User: {scan['username']}",
                    color=color,
                    timestamp=datetime.fromisoformat(scan['created_at'])
                )
                embed.add_field(name="Files Scanned", value=str(scan['files_scanned']), inline=True)
                embed.add_field(name="Threats Found", value=str(scan['threats_found']), inline=True)
                
                # Parse JSON fields
                r6_accounts = json.loads(scan['r6_accounts']) if scan['r6_accounts'] else []
                steam_accounts = json.loads(scan['steam_accounts']) if scan['steam_accounts'] else []
                
                embed.add_field(name="R6 Accounts", value=str(len(r6_accounts)), inline=True)
                embed.add_field(name="Steam Accounts", value=str(len(steam_accounts)), inline=True)
                
                await ctx.send(embed=embed)
            else:
                await ctx.send("📭 No scans found")
        
        @bot.command(name='status')
        async def status_command(ctx):
            """Check bot status"""
            if str(ctx.channel.id) != channel_id:
                return
                
            embed = discord.Embed(
                title="🟢 Bot Status",
                description="R6X CyberScan Bot is operational",
                color=0x00FF9D
            )
            embed.add_field(name="Latency", value=f"{round(bot.latency * 1000)}ms", inline=True)
            embed.add_field(name="User", value=username, inline=True)
            await ctx.send(embed=embed)
        
        @bot.command(name='help')
        async def help_command(ctx):
            """Show commands"""
            if str(ctx.channel.id) != channel_id:
                return
                
            embed = discord.Embed(
                title="🤖 R6X Bot Commands",
                description="Available commands",
                color=0x5865F2
            )
            embed.add_field(name="!scan", value="Get your latest scan", inline=False)
            embed.add_field(name="!status", value="Check bot status", inline=False)
            embed.add_field(name="!help", value="Show this message", inline=False)
            await ctx.send(embed=embed)
        
        # Start bot
        await bot.start(bot_token)
    
    async def stop_bot(self, user_id: str):
        """Stop a user's bot"""
        if user_id in self.bots:
            try:
                bot = self.bots[user_id]
                await bot.close()
                del self.bots[user_id]
                
                if user_id in self.bot_tasks:
                    self.bot_tasks[user_id].cancel()
                    del self.bot_tasks[user_id]
                
                service_supabase.table("discord_bots")\
                    .update({"status": "disconnected", "disconnected_at": datetime.utcnow().isoformat()})\
                    .eq("user_id", user_id)\
                    .execute()
                
                logger.info(f"✅ Bot stopped for user {user_id}")
                return True
            except Exception as e:
                logger.error(f"Error stopping bot: {e}")
        return False
    
    async def send_scan_to_discord(self, user_id: str, scan_data: Dict):
        """Send scan results to user's Discord channel"""
        if user_id not in self.bots:
            return False
            
        try:
            bot = self.bots[user_id]
            
            # Get channel ID from database
            result = service_supabase.table("discord_bots")\
                .select("channel_id")\
                .eq("user_id", user_id)\
                .execute()
            
            if not result.data:
                return False
                
            channel_id = result.data[0]["channel_id"]
            channel = bot.get_channel(int(channel_id))
            
            if not channel:
                for guild in bot.guilds:
                    channel = guild.get_channel(int(channel_id))
                    if channel:
                        break
            
            if not channel:
                return False
            
            # Create main embed
            color = 0x00FF9D if scan_data['threats_found'] == 0 else 0xFF003C
            embed = discord.Embed(
                title=f"📊 Scan Results: {scan_data['username']}",
                description=f"Computer: {scan_data['computer']}",
                color=color,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(name="📁 Files Scanned", value=str(scan_data['files_scanned']), inline=True)
            embed.add_field(name="🚨 Threats Found", value=str(scan_data['threats_found']), inline=True)
            embed.add_field(name="🎮 R6 Accounts", value=str(len(scan_data['r6_accounts'])), inline=True)
            embed.add_field(name="🔄 Steam Accounts", value=str(len(scan_data['steam_accounts'])), inline=True)
            
            if scan_data.get('windows_install_date'):
                embed.add_field(name="💻 Windows Install", value=scan_data['windows_install_date'][:10], inline=True)
            
            await channel.send(embed=embed)
            
            # Send suspicious files if any
            if scan_data['suspicious_files']:
                sus_embed = discord.Embed(
                    title="⚠️ Suspicious Files Found",
                    color=0xFF003C
                )
                sus_list = ""
                for f in scan_data['suspicious_files'][:10]:
                    sus_list += f"• {f.get('name', 'Unknown')} ({f.get('severity', 'MEDIUM')})\n"
                if sus_list:
                    sus_embed.description = sus_list
                    await channel.send(embed=sus_embed)
            
            return True
        except Exception as e:
            logger.error(f"Error sending to Discord: {e}")
            return False
    
    def get_bot_status(self, user_id: str):
        """Check if bot is running for user"""
        return user_id in self.bots

# Initialize bot host
bot_host = DiscordBotHost()

# ========== FASTAPI APP ==========
app = FastAPI(title="R6X CYBERSCAN API")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, config.SECRET_KEY, algorithm=config.ALGORITHM)

def create_refresh_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=config.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, config.SECRET_KEY, algorithm=config.ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme)):
    if not token:
        return None
    try:
        payload = jwt.decode(token, config.SECRET_KEY, algorithms=[config.ALGORITHM])
        username = payload.get("sub")
        if username:
            result = supabase.table("users").select("*").eq("username", username).execute()
            if result.data and len(result.data) > 0:
                return result.data[0]
    except:
        pass
    return None

async def get_current_admin(current_user: dict = Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Not authenticated")
    
    is_admin = current_user.get("role") == "owner" or current_user.get("user_id") in config.ADMIN_IDS
    if not is_admin:
        raise HTTPException(403, "Admin access required")
    
    return current_user

# ========== API ROUTES ==========

@app.get("/")
async def root():
    return {
        "service": "R6X CYBERSCAN API",
        "status": "online",
        "bots_active": len(bot_host.bots),
        "version": "4.0.0"
    }

@app.get("/api/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

# ========== AUTH ROUTES ==========

@app.post("/api/auth/register", response_model=Token)
async def register(user: UserCreate):
    # Check if user exists
    result = supabase.table("users").select("*")\
        .or_("username.eq.{},email.eq.{}".format(user.username, user.email))\
        .execute()
    
    if result.data and len(result.data) > 0:
        raise HTTPException(400, "Username or email already registered")
    
    # Hash password
    hashed = bcrypt.hashpw(user.password.encode(), bcrypt.gensalt())
    
    # Create user
    user_id = str(uuid.uuid4())
    user_doc = {
        "user_id": user_id,
        "username": user.username,
        "email": user.email,
        "hashed_password": hashed.decode(),
        "role": "owner" if user.username == "xotiic" else "user",
        "created_at": datetime.utcnow().isoformat(),
        "is_active": True,
        "total_scans": 0,
        "threats_found": 0
    }
    
    service_supabase.table("users").insert(user_doc).execute()
    
    # Create tokens
    access_token = create_access_token({"sub": user.username, "user_id": user_id})
    refresh_token = create_refresh_token({"sub": user.username, "user_id": user_id})
    
    # Store refresh token
    service_supabase.table("tokens").insert({
        "user_id": user_id,
        "refresh_token": refresh_token,
        "created_at": datetime.utcnow().isoformat(),
        "expires_at": (datetime.utcnow() + timedelta(days=config.REFRESH_TOKEN_EXPIRE_DAYS)).isoformat()
    }).execute()
    
    logger.info(f"✅ New user registered: {user.username}")
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }

@app.post("/api/auth/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    # Find user
    result = supabase.table("users").select("*")\
        .eq("username", form_data.username)\
        .execute()
    
    if not result.data or len(result.data) == 0:
        result = supabase.table("users").select("*")\
            .eq("email", form_data.username)\
            .execute()
    
    if not result.data or len(result.data) == 0:
        raise HTTPException(401, "Invalid credentials")
    
    user = result.data[0]
    
    # Check password
    if not bcrypt.checkpw(form_data.password.encode(), user["hashed_password"].encode()):
        raise HTTPException(401, "Invalid credentials")
    
    # Update last login
    service_supabase.table("users").update({
        "last_login": datetime.utcnow().isoformat()
    }).eq("user_id", user["user_id"]).execute()
    
    # Create tokens
    access_token = create_access_token({"sub": user["username"], "user_id": user["user_id"]})
    refresh_token = create_refresh_token({"sub": user["username"], "user_id": user["user_id"]})
    
    # Store refresh token
    service_supabase.table("tokens").insert({
        "user_id": user["user_id"],
        "refresh_token": refresh_token,
        "created_at": datetime.utcnow().isoformat(),
        "expires_at": (datetime.utcnow() + timedelta(days=config.REFRESH_TOKEN_EXPIRE_DAYS)).isoformat()
    }).execute()
    
    logger.info(f"✅ User logged in: {form_data.username}")
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }

@app.get("/api/auth/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Not authenticated")
    
    return {
        "user_id": current_user["user_id"],
        "username": current_user["username"],
        "email": current_user["email"],
        "role": current_user.get("role", "user"),
        "is_admin": current_user.get("role") == "owner" or current_user.get("user_id") in config.ADMIN_IDS,
        "total_scans": current_user.get("total_scans", 0),
        "threats_found": current_user.get("threats_found", 0),
        "created_at": current_user.get("created_at")
    }

@app.post("/api/auth/logout")
async def logout(current_user: dict = Depends(get_current_user)):
    if current_user:
        service_supabase.table("tokens").delete().eq("user_id", current_user["user_id"]).execute()
        await bot_host.stop_bot(current_user["user_id"])
    return {"success": True}

@app.post("/api/auth/refresh", response_model=Token)
async def refresh_token(refresh_token: str):
    try:
        payload = jwt.decode(refresh_token, config.SECRET_KEY, algorithms=[config.ALGORITHM])
        username = payload.get("sub")
        user_id = payload.get("user_id")
        
        # Check if token exists
        result = supabase.table("tokens").select("*")\
            .eq("refresh_token", refresh_token)\
            .eq("user_id", user_id)\
            .execute()
        
        if not result.data or len(result.data) == 0:
            raise HTTPException(401, "Invalid refresh token")
        
        # Create new tokens
        access_token = create_access_token({"sub": username, "user_id": user_id})
        new_refresh_token = create_refresh_token({"sub": username, "user_id": user_id})
        
        # Remove old token
        service_supabase.table("tokens").delete().eq("refresh_token", refresh_token).execute()
        
        # Store new token
        service_supabase.table("tokens").insert({
            "user_id": user_id,
            "refresh_token": new_refresh_token,
            "created_at": datetime.utcnow().isoformat(),
            "expires_at": (datetime.utcnow() + timedelta(days=config.REFRESH_TOKEN_EXPIRE_DAYS)).isoformat()
        }).execute()
        
        return {
            "access_token": access_token,
            "refresh_token": new_refresh_token,
            "token_type": "bearer"
        }
    except:
        raise HTTPException(401, "Invalid refresh token")

# ========== DISCORD BOT ROUTES ==========

@app.post("/api/bot/start")
async def start_bot(
    bot_connect: BotConnect,
    current_user: dict = Depends(get_current_user)
):
    """Start a Discord bot for the current user"""
    if not current_user:
        raise HTTPException(401, "Not authenticated")
    
    # Check if user is authorized to start this bot
    if bot_connect.user_id != current_user["user_id"] and current_user.get("role") != "owner":
        raise HTTPException(403, "Not authorized")
    
    # Stop existing bot if any
    await bot_host.stop_bot(bot_connect.user_id)
    
    # Start new bot
    bot_host.start_bot_for_user(
        user_id=bot_connect.user_id,
        bot_token=bot_connect.bot_token,
        channel_id=bot_connect.channel_id
    )
    
    return {"status": "starting", "message": "Bot is starting..."}

@app.post("/api/bot/stop")
async def stop_bot(
    user_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Stop a user's bot"""
    if not current_user:
        raise HTTPException(401, "Not authenticated")
    
    if user_id != current_user["user_id"] and current_user.get("role") != "owner":
        raise HTTPException(403, "Not authorized")
    
    success = await bot_host.stop_bot(user_id)
    return {"status": "stopped" if success else "not_found"}

@app.get("/api/bot/status")
async def bot_status(
    user_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get bot status for a user"""
    if not current_user:
        raise HTTPException(401, "Not authenticated")
    
    if user_id != current_user["user_id"] and current_user.get("role") != "owner":
        raise HTTPException(403, "Not authorized")
    
    is_running = bot_host.get_bot_status(user_id)
    
    result = supabase.table("discord_bots")\
        .select("*")\
        .eq("user_id", user_id)\
        .execute()
    
    bot_info = result.data[0] if result.data else None
    
    return {
        "connected": is_running,
        "channel_id": bot_info.get("channel_id") if bot_info else None,
        "bot_name": bot_info.get("bot_name") if bot_info else None,
        "status": "connected" if is_running else "disconnected"
    }

# ========== SCAN ROUTES ==========

@app.post("/api/scan/save")
async def save_scan(
    scan: ScanResult,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Save scan results and send to Discord if bot connected"""
    if not current_user:
        raise HTTPException(401, "Not authenticated")
    
    # Save to database
    scan_id = str(uuid.uuid4())
    scan_doc = {
        "scan_id": scan_id,
        "user_id": current_user["user_id"],
        "username": scan.username,
        "computer": scan.computer,
        "files_scanned": scan.files_scanned,
        "threats_found": scan.threats_found,
        "r6_accounts": json.dumps(scan.r6_accounts),
        "steam_accounts": json.dumps(scan.steam_accounts),
        "suspicious_files": json.dumps(scan.suspicious_files),
        "windows_install_date": scan.windows_install_date,
        "antivirus_status": scan.antivirus_status,
        "prefetch_files": json.dumps(scan.prefetch_files) if scan.prefetch_files else None,
        "logitech_scripts": json.dumps(scan.logitech_scripts) if scan.logitech_scripts else None,
        "scan_time": scan.scan_time,
        "created_at": datetime.utcnow().isoformat()
    }
    
    service_supabase.table("scans").insert(scan_doc).execute()
    
    # Update user stats
    service_supabase.table("users").update({
        "total_scans": current_user["total_scans"] + 1,
        "threats_found": current_user["threats_found"] + scan.threats_found
    }).eq("user_id", current_user["user_id"]).execute()
    
    # Send to Discord if bot connected
    if current_user["user_id"] in bot_host.bots:
        background_tasks.add_task(
            bot_host.send_scan_to_discord,
            current_user["user_id"],
            scan.dict()
        )
    
    return {"success": True, "scan_id": scan_id}

@app.get("/api/scan/history")
async def get_history(
    limit: int = 50,
    current_user: dict = Depends(get_current_user)
):
    """Get user's scan history"""
    if not current_user:
        raise HTTPException(401, "Not authenticated")
    
    result = supabase.table("scans").select("*")\
        .eq("user_id", current_user["user_id"])\
        .order("created_at", desc=True)\
        .limit(limit)\
        .execute()
    
    scans = result.data if result.data else []
    
    # Parse JSON fields
    for scan in scans:
        if scan.get("r6_accounts"):
            scan["r6_accounts"] = json.loads(scan["r6_accounts"])
        if scan.get("steam_accounts"):
            scan["steam_accounts"] = json.loads(scan["steam_accounts"])
        if scan.get("suspicious_files"):
            scan["suspicious_files"] = json.loads(scan["suspicious_files"])
        if scan.get("prefetch_files"):
            scan["prefetch_files"] = json.loads(scan["prefetch_files"])
        if scan.get("logitech_scripts"):
            scan["logitech_scripts"] = json.loads(scan["logitech_scripts"])
    
    return {"scans": scans}

@app.get("/api/scan/{scan_id}")
async def get_scan(
    scan_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get specific scan details"""
    if not current_user:
        raise HTTPException(401, "Not authenticated")
    
    result = supabase.table("scans").select("*")\
        .eq("scan_id", scan_id)\
        .eq("user_id", current_user["user_id"])\
        .execute()
    
    if not result.data or len(result.data) == 0:
        raise HTTPException(404, "Scan not found")
    
    scan = result.data[0]
    
    # Parse JSON fields
    if scan.get("r6_accounts"):
        scan["r6_accounts"] = json.loads(scan["r6_accounts"])
    if scan.get("steam_accounts"):
        scan["steam_accounts"] = json.loads(scan["steam_accounts"])
    if scan.get("suspicious_files"):
        scan["suspicious_files"] = json.loads(scan["suspicious_files"])
    if scan.get("prefetch_files"):
        scan["prefetch_files"] = json.loads(scan["prefetch_files"])
    if scan.get("logitech_scripts"):
        scan["logitech_scripts"] = json.loads(scan["logitech_scripts"])
    
    return scan

# ========== ADMIN ROUTES ==========

@app.get("/api/admin/users")
async def get_users(current_user: dict = Depends(get_current_admin)):
    """Get all users (admin only)"""
    result = supabase.table("users").select("*")\
        .order("created_at")\
        .execute()
    
    users = result.data if result.data else []
    
    # Remove sensitive data
    for user in users:
        if "hashed_password" in user:
            del user["hashed_password"]
    
    return {"users": users}

@app.get("/api/admin/stats")
async def get_stats(current_user: dict = Depends(get_current_admin)):
    """Get system stats (admin only)"""
    users_result = supabase.table("users").select("*", count="exact").execute()
    scans_result = supabase.table("scans").select("*", count="exact").execute()
    
    return {
        "total_users": users_result.count if hasattr(users_result, 'count') else 0,
        "total_scans": scans_result.count if hasattr(scans_result, 'count') else 0,
        "active_bots": len(bot_host.bots),
        "admins": config.ADMIN_IDS
    }

# ========== STARTUP ==========
@app.on_event("startup")
async def startup():
    logger.info("🚀 R6X CYBERSCAN Backend starting...")
    logger.info(f"Admin IDs: {config.ADMIN_IDS}")
    
    # Check if owner exists
    result = supabase.table("users").select("*").eq("username", "xotiic").execute()
    
    if not result.data or len(result.data) == 0:
        hashed = bcrypt.hashpw(b"40671Mps19*", bcrypt.gensalt())
        owner_doc = {
            "user_id": str(uuid.uuid4()),
            "username": "xotiic",
            "email": "owner@r6x.com",
            "hashed_password": hashed.decode(),
            "role": "owner",
            "created_at": datetime.utcnow().isoformat(),
            "is_active": True,
            "total_scans": 0,
            "threats_found": 0
        }
        service_supabase.table("users").insert(owner_doc).execute()
        logger.info("✅ Owner account created")

@app.on_event("shutdown")
async def shutdown():
    """Stop all bots on shutdown"""
    logger.info("Shutting down...")
    for user_id in list(bot_host.bots.keys()):
        await bot_host.stop_bot(user_id)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
