# backend.py - R6X CYBERSCAN Production Backend with Fixed Discord Integration
# Deploy this on Render.com

import os
import jwt
import bcrypt
import datetime
import asyncio
import uuid
import json
import threading  # <-- This was missing!
import traceback
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Depends, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
import motor.motor_asyncio
import logging
from datetime import datetime, timedelta
import secrets

# Discord imports
import discord
from discord.ext import commands

# Configuration
class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_urlsafe(32))
    ALGORITHM = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES = 30
    REFRESH_TOKEN_EXPIRE_DAYS = 7
    MONGODB_URL = os.getenv("MONGODB_URL", "mongodb+srv://xotiicglizzy_db_user:WBnaZXuhxBWzLwx5@cluster0.cvidwug.mongodb.net/")
    DATABASE_NAME = "r6x_cyberscan"
    API_VERSION = "4.0.0"
    ENVIRONMENT = os.getenv("ENVIRONMENT", "production")
    
config = Config()

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("r6x-backend")

# Database setup
try:
    logger.info(f"Connecting to MongoDB...")
    client = motor.motor_asyncio.AsyncIOMotorClient(config.MONGODB_URL)
    db = client[config.DATABASE_NAME]
    
    # Collections
    users_collection = db["users"]
    scans_collection = db["scans"]
    tokens_collection = db["tokens"]
    bots_collection = db["discord_bots"]
    threats_collection = db["threats"]
    
    # Test connection
    await client.admin.command('ping')
    logger.info("✅ Connected to MongoDB Atlas successfully")
except Exception as e:
    logger.error(f"❌ Failed to connect to MongoDB: {e}")
    raise

# Pydantic models
class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8)

class ScanResult(BaseModel):
    name: str
    files_scanned: int
    threats_found: int
    r6_accounts: List[Dict]
    steam_accounts: List[Dict]
    suspicious_files: List[Dict]
    scan_duration: float
    status: str
    system_info: Optional[Dict] = None

class DiscordBotConnect(BaseModel):
    token: str
    channel_id: str

class DiscordMessage(BaseModel):
    channel_id: str
    content: str
    results: Optional[Dict] = None

class RoleUpdate(BaseModel):
    username: str
    role: str

# Discord Bot Manager - Fixed with proper threading import
class DiscordBotManager:
    def __init__(self):
        self.bots = {}  # user_id -> bot instance
        self.bot_tasks = {}  # user_id -> task
        self.loop = None
        self.thread = None
        self._start_loop()
        logger.info("✅ Discord Bot Manager initialized")
        
    def _start_loop(self):
        """Start the asyncio event loop in a separate thread"""
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        logger.info("✅ Discord bot loop started in thread")
        
    def _run_loop(self):
        """Run the asyncio event loop"""
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()
        
    async def start_bot(self, token: str, channel_id: str, user_id: str):
        """Start a Discord bot instance for a user"""
        try:
            bot_id = str(uuid.uuid4())
            
            # Configure bot intents
            intents = discord.Intents.default()
            intents.message_content = True
            intents.guilds = True
            
            bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)
            
            @bot.event
            async def on_ready():
                logger.info(f"✅ Bot {bot.user} connected for user {user_id}")
                
                # Get the channel
                channel = None
                for guild in bot.guilds:
                    channel = guild.get_channel(int(channel_id))
                    if channel:
                        break
                
                if not channel:
                    try:
                        channel = await bot.fetch_channel(int(channel_id))
                    except:
                        logger.error(f"Could not find channel {channel_id}")
                        return
                
                # Send connection success message
                embed = discord.Embed(
                    title="🤖 R6X Bot Connected",
                    description="Your security scanner bot is now active!",
                    color=0x00FF9D,
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Status", value="🟢 Online", inline=True)
                embed.add_field(name="Commands", value="!scan, !status, !help", inline=True)
                embed.set_footer(text="R6X CyberScan")
                
                await channel.send(embed=embed)
                
                # Store bot info in database
                await bots_collection.update_one(
                    {"user_id": user_id},
                    {"$set": {
                        "bot_id": bot_id,
                        "token_preview": token[:10] + "...",
                        "channel_id": channel_id,
                        "bot_name": str(bot.user),
                        "status": "connected",
                        "connected_at": datetime.utcnow()
                    }},
                    upsert=True
                )
                
            @bot.event
            async def on_command_error(ctx, error):
                if isinstance(error, commands.CommandNotFound):
                    return
                logger.error(f"Bot error for user {user_id}: {error}")
                await ctx.send(f"❌ Error: {str(error)}")
                
            @bot.command(name='scan')
            async def scan_command(ctx):
                """Get latest scan results"""
                if str(ctx.channel.id) != channel_id:
                    return
                
                # Get latest scan for user
                latest_scan = await scans_collection.find_one(
                    {"user_id": user_id},
                    sort=[("created_at", -1)]
                )
                
                if latest_scan:
                    color = 0x00FF9D if latest_scan['threats_found'] == 0 else 0xFF003C
                    
                    embed = discord.Embed(
                        title="📊 Latest Scan Results",
                        description=f"Scan: {latest_scan['name']}",
                        color=color,
                        timestamp=latest_scan['created_at']
                    )
                    embed.add_field(name="Files Scanned", value=str(latest_scan['files_scanned']), inline=True)
                    embed.add_field(name="Threats Found", value=str(latest_scan['threats_found']), inline=True)
                    embed.add_field(name="Duration", value=f"{latest_scan['scan_duration']:.2f}s", inline=True)
                    
                    if latest_scan.get('r6_accounts'):
                        embed.add_field(name="R6 Accounts", value=str(len(latest_scan['r6_accounts'])), inline=True)
                    
                    if latest_scan.get('steam_accounts'):
                        embed.add_field(name="Steam Accounts", value=str(len(latest_scan['steam_accounts'])), inline=True)
                    
                    embed.set_footer(text=f"User: {latest_scan['username']}")
                    await ctx.send(embed=embed)
                else:
                    await ctx.send("📭 No scans found for your account")
                    
            @bot.command(name='help')
            async def help_command(ctx):
                """Show available commands"""
                if str(ctx.channel.id) != channel_id:
                    return
                    
                embed = discord.Embed(
                    title="🤖 R6X Bot Commands",
                    description="Available commands for this channel",
                    color=0x5865F2
                )
                embed.add_field(name="!scan", value="Get your latest scan results", inline=False)
                embed.add_field(name="!status", value="Check bot status", inline=False)
                embed.add_field(name="!help", value="Show this message", inline=False)
                embed.set_footer(text="R6X CyberScan v4.0")
                
                await ctx.send(embed=embed)
                
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
                embed.add_field(name="Connected Since", value=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), inline=True)
                
                await ctx.send(embed=embed)
            
            # Start the bot in the event loop
            future = asyncio.run_coroutine_threadsafe(bot.start(token), self.loop)
            self.bot_tasks[user_id] = future
            self.bots[user_id] = {
                "bot": bot,
                "channel_id": channel_id,
                "bot_id": bot_id
            }
            
            return bot_id
            
        except Exception as e:
            logger.error(f"Failed to start bot for user {user_id}: {e}")
            logger.error(traceback.format_exc())
            raise
            
    async def send_scan_results(self, user_id: str, channel_id: str, scan_data: Dict):
        """Send scan results to Discord"""
        if user_id not in self.bots:
            logger.error(f"No bot found for user {user_id}")
            return False
            
        try:
            bot_data = self.bots[user_id]
            bot = bot_data["bot"]
            
            # Find the channel
            channel = bot.get_channel(int(channel_id))
            if not channel:
                for guild in bot.guilds:
                    channel = guild.get_channel(int(channel_id))
                    if channel:
                        break
            
            if not channel:
                logger.error(f"Channel {channel_id} not found for user {user_id}")
                return False
            
            # Create main embed
            color = 0x00FF9D if scan_data.get('threats_found', 0) == 0 else 0xFF003C
            
            main_embed = discord.Embed(
                title=f"📊 Scan Complete: {scan_data.get('name', 'Unknown')}",
                description=f"Security scan finished at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}",
                color=color,
                timestamp=datetime.utcnow()
            )
            
            # Statistics
            main_embed.add_field(name="📁 Files Scanned", value=str(scan_data.get('files_scanned', 0)), inline=True)
            main_embed.add_field(name="🚨 Threats Found", value=str(scan_data.get('threats_found', 0)), inline=True)
            main_embed.add_field(name="⏱️ Duration", value=f"{scan_data.get('scan_duration', 0):.2f}s", inline=True)
            
            # Account info
            r6_count = len(scan_data.get('r6_accounts', []))
            steam_count = len(scan_data.get('steam_accounts', []))
            main_embed.add_field(name="🎮 R6 Accounts", value=str(r6_count), inline=True)
            main_embed.add_field(name="🔄 Steam Accounts", value=str(steam_count), inline=True)
            
            # System info
            if scan_data.get('system_info'):
                sys_info = scan_data['system_info']
                main_embed.add_field(name="💻 System", value=sys_info.get('computer_name', 'Unknown'), inline=True)
                main_embed.add_field(name="👤 User", value=sys_info.get('user_name', 'Unknown'), inline=True)
            
            main_embed.set_footer(text="R6X CyberScan v4.0")
            
            await channel.send(embed=main_embed)
            
            # Send threats if any
            if scan_data.get('threats_found', 0) > 0 and scan_data.get('suspicious_files'):
                threat_embed = discord.Embed(
                    title="⚠️ Detected Threats",
                    color=0xFF003C
                )
                
                threat_list = ""
                for threat in scan_data['suspicious_files'][:10]:
                    threat_list += f"• **{threat.get('name', 'Unknown')}** - Severity: {threat.get('severity', 'MEDIUM')}\n"
                
                if threat_list:
                    threat_embed.description = threat_list
                    await channel.send(embed=threat_embed)
            
            logger.info(f"✅ Scan results sent to Discord for user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send scan results to Discord: {e}")
            logger.error(traceback.format_exc())
            return False
            
    async def disconnect_bot(self, user_id: str):
        """Disconnect user's bot"""
        if user_id in self.bots:
            try:
                bot = self.bots[user_id]["bot"]
                
                # Close the bot connection
                if bot.is_ready():
                    asyncio.run_coroutine_threadsafe(bot.close(), self.loop)
                
                # Cancel the task
                if user_id in self.bot_tasks:
                    self.bot_tasks[user_id].cancel()
                    
                del self.bots[user_id]
                if user_id in self.bot_tasks:
                    del self.bot_tasks[user_id]
                
                await bots_collection.update_one(
                    {"user_id": user_id},
                    {"$set": {"status": "disconnected", "disconnected_at": datetime.utcnow()}}
                )
                
                logger.info(f"✅ Bot disconnected for user {user_id}")
                return True
            except Exception as e:
                logger.error(f"Error disconnecting bot for user {user_id}: {e}")
                return False
        return False
        
    async def disconnect_all_bots(self):
        """Disconnect all bots"""
        for user_id in list(self.bots.keys()):
            await self.disconnect_bot(user_id)

# Initialize Discord Bot Manager
discord_manager = DiscordBotManager()

# FastAPI app
app = FastAPI(
    title="R6X CYBERSCAN API",
    version=config.API_VERSION,
    description="Enterprise Security Scanner Backend",
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login", auto_error=False)

# Helper functions
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire, "type": "access"})
    encoded_jwt = jwt.encode(to_encode, config.SECRET_KEY, algorithm=config.ALGORITHM)
    return encoded_jwt

def create_refresh_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=config.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    encoded_jwt = jwt.encode(to_encode, config.SECRET_KEY, algorithm=config.ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme)):
    if not token:
        return None
        
    try:
        payload = jwt.decode(token, config.SECRET_KEY, algorithms=[config.ALGORITHM])
        username: str = payload.get("sub")
        
        if username is None:
            return None
            
        user = await users_collection.find_one({"username": username})
        return user
    except:
        return None

async def get_current_owner(current_user: dict = Depends(get_current_user)):
    if not current_user or current_user.get("role") != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner access required"
        )
    return current_user

# API Routes
@app.get("/")
async def root():
    return {
        "service": "R6X CYBERSCAN API",
        "version": config.API_VERSION,
        "status": "operational",
        "bots_active": len(discord_manager.bots),
        "timestamp": datetime.utcnow().isoformat()
    }

@app.get("/api/health")
async def health_check():
    try:
        await db.command("ping")
        db_status = "connected"
    except:
        db_status = "disconnected"
        
    return {
        "status": "healthy",
        "database": db_status,
        "bots_active": len(discord_manager.bots),
        "timestamp": datetime.utcnow().isoformat()
    }

@app.post("/api/auth/register", response_model=Token)
async def register(user: UserCreate):
    # Check if user exists
    existing_user = await users_collection.find_one({
        "$or": [
            {"username": user.username},
            {"email": user.email}
        ]
    })
    
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username or email already registered"
        )
    
    # Hash password
    hashed_password = bcrypt.hashpw(user.password.encode('utf-8'), bcrypt.gensalt())
    
    # Create user document
    user_doc = {
        "user_id": str(uuid.uuid4()),
        "username": user.username,
        "email": user.email,
        "hashed_password": hashed_password.decode('utf-8'),
        "role": "user",
        "created_at": datetime.utcnow(),
        "last_login": None,
        "is_active": True,
        "total_scans": 0,
        "threats_found": 0
    }
    
    await users_collection.insert_one(user_doc)
    
    # Create tokens
    access_token = create_access_token(
        data={"sub": user.username, "user_id": user_doc["user_id"]}
    )
    refresh_token = create_refresh_token(
        data={"sub": user.username, "user_id": user_doc["user_id"]}
    )
    
    await tokens_collection.insert_one({
        "user_id": user_doc["user_id"],
        "refresh_token": refresh_token,
        "created_at": datetime.utcnow(),
        "expires_at": datetime.utcnow() + timedelta(days=config.REFRESH_TOKEN_EXPIRE_DAYS)
    })
    
    logger.info(f"✅ New user registered: {user.username}")
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }

@app.post("/api/auth/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    # Find user
    user = await users_collection.find_one({"username": form_data.username})
    
    if not user:
        user = await users_collection.find_one({"email": form_data.username})
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )
    
    # Verify password
    if not bcrypt.checkpw(
        form_data.password.encode('utf-8'),
        user["hashed_password"].encode('utf-8')
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )
    
    # Update last login
    await users_collection.update_one(
        {"user_id": user["user_id"]},
        {"$set": {"last_login": datetime.utcnow()}}
    )
    
    # Create tokens
    access_token = create_access_token(
        data={"sub": user["username"], "user_id": user["user_id"]}
    )
    refresh_token = create_refresh_token(
        data={"sub": user["username"], "user_id": user["user_id"]}
    )
    
    await tokens_collection.insert_one({
        "user_id": user["user_id"],
        "refresh_token": refresh_token,
        "created_at": datetime.utcnow(),
        "expires_at": datetime.utcnow() + timedelta(days=config.REFRESH_TOKEN_EXPIRE_DAYS)
    })
    
    logger.info(f"✅ User logged in: {form_data.username}")
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }

@app.post("/api/auth/refresh", response_model=Token)
async def refresh_token(refresh_token: str):
    try:
        payload = jwt.decode(refresh_token, config.SECRET_KEY, algorithms=[config.ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type"
            )
            
        username = payload.get("sub")
        user_id = payload.get("user_id")
        
        # Check if token exists
        stored_token = await tokens_collection.find_one({
            "refresh_token": refresh_token,
            "user_id": user_id
        })
        
        if not stored_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token not found"
            )
        
        # Create new tokens
        new_access_token = create_access_token(
            data={"sub": username, "user_id": user_id}
        )
        new_refresh_token = create_refresh_token(
            data={"sub": username, "user_id": user_id}
        )
        
        # Remove old token
        await tokens_collection.delete_one({"refresh_token": refresh_token})
        
        # Store new token
        await tokens_collection.insert_one({
            "user_id": user_id,
            "refresh_token": new_refresh_token,
            "created_at": datetime.utcnow(),
            "expires_at": datetime.utcnow() + timedelta(days=config.REFRESH_TOKEN_EXPIRE_DAYS)
        })
        
        return {
            "access_token": new_access_token,
            "refresh_token": new_refresh_token,
            "token_type": "bearer"
        }
        
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token expired"
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )

@app.get("/api/auth/me")
async def get_current_user_info(current_user: dict = Depends(get_current_user)):
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
        
    return {
        "id": current_user["user_id"],
        "username": current_user["username"],
        "email": current_user["email"],
        "role": current_user["role"],
        "created_at": current_user["created_at"].isoformat(),
        "last_login": current_user.get("last_login").isoformat() if current_user.get("last_login") else None,
        "is_active": current_user.get("is_active", True),
        "total_scans": current_user.get("total_scans", 0),
        "threats_found": current_user.get("threats_found", 0)
    }

@app.post("/api/auth/logout")
async def logout(current_user: dict = Depends(get_current_user)):
    if current_user:
        await tokens_collection.delete_many({"user_id": current_user["user_id"]})
        await discord_manager.disconnect_bot(current_user["user_id"])
    return {"success": True, "message": "Logged out successfully"}

@app.post("/api/discord/connect")
async def connect_discord_bot(
    connection: DiscordBotConnect,
    current_user: dict = Depends(get_current_user)
):
    """Connect a Discord bot for the current user"""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    
    try:
        # Validate token format
        if len(connection.token) < 50:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Discord token format"
            )
        
        # Disconnect any existing bot
        await discord_manager.disconnect_bot(current_user["user_id"])
        
        # Start new bot
        bot_id = await discord_manager.start_bot(
            token=connection.token,
            channel_id=connection.channel_id,
            user_id=current_user["user_id"]
        )
        
        return {
            "success": True,
            "bot_id": bot_id,
            "message": "Bot connected successfully"
        }
        
    except Exception as e:
        logger.error(f"Failed to connect bot: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to connect bot: {str(e)}"
        )

@app.post("/api/discord/send")
async def send_discord_message(
    message: DiscordMessage,
    current_user: dict = Depends(get_current_user)
):
    """Send a message using user's connected Discord bot"""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    
    success = await discord_manager.send_scan_results(
        user_id=current_user["user_id"],
        channel_id=message.channel_id,
        scan_data=message.results if message.results else {}
    )
    
    if success:
        return {"success": True, "message": "Results sent to Discord"}
    else:
        return {"success": False, "message": "Failed to send to Discord - check bot connection"}

@app.post("/api/discord/disconnect")
async def disconnect_discord_bot(current_user: dict = Depends(get_current_user)):
    """Disconnect user's Discord bot"""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    
    success = await discord_manager.disconnect_bot(current_user["user_id"])
    
    if success:
        return {"success": True, "message": "Bot disconnected"}
    else:
        return {"success": False, "message": "No bot connected"}

@app.get("/api/discord/status")
async def get_bot_status(current_user: dict = Depends(get_current_user)):
    """Get user's Discord bot status"""
    if not current_user:
        return {"connected": False, "status": "not_authenticated"}
    
    is_connected = current_user["user_id"] in discord_manager.bots
    bot_info = await bots_collection.find_one({"user_id": current_user["user_id"]})
    
    return {
        "connected": is_connected,
        "channel_id": bot_info.get("channel_id") if bot_info else None,
        "bot_name": bot_info.get("bot_name") if bot_info else None,
        "status": "connected" if is_connected else "disconnected"
    }

@app.post("/api/scans/save")
async def save_scan_result(
    scan: ScanResult,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Save scan results to database and optionally send to Discord"""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    
    # Save to database
    scan_doc = {
        "scan_id": str(uuid.uuid4()),
        "user_id": current_user["user_id"],
        "username": current_user["username"],
        "name": scan.name,
        "files_scanned": scan.files_scanned,
        "threats_found": scan.threats_found,
        "r6_accounts": [dict(acc) for acc in scan.r6_accounts],
        "steam_accounts": [dict(acc) for acc in scan.steam_accounts],
        "suspicious_files": [dict(f) for f in scan.suspicious_files],
        "scan_duration": scan.scan_duration,
        "status": scan.status,
        "system_info": dict(scan.system_info) if scan.system_info else None,
        "created_at": datetime.utcnow()
    }
    
    await scans_collection.insert_one(scan_doc)
    
    # Update user stats
    await users_collection.update_one(
        {"user_id": current_user["user_id"]},
        {
            "$inc": {
                "total_scans": 1,
                "threats_found": scan.threats_found
            }
        }
    )
    
    # Log threats
    if scan.threats_found > 0:
        for threat in scan.suspicious_files:
            threat_doc = {
                "threat_id": str(uuid.uuid4()),
                "user_id": current_user["user_id"],
                "username": current_user["username"],
                "scan_id": scan_doc["scan_id"],
                "threat_name": threat.get("name", "Unknown"),
                "severity": threat.get("severity", "MEDIUM"),
                "file_path": threat.get("path", ""),
                "action_taken": "Logged",
                "timestamp": datetime.utcnow()
            }
            await threats_collection.insert_one(threat_doc)
    
    # Auto-send to Discord if bot is connected
    if current_user["user_id"] in discord_manager.bots:
        bot_info = await bots_collection.find_one({"user_id": current_user["user_id"]})
        if bot_info and bot_info.get("channel_id"):
            # Send in background
            background_tasks.add_task(
                discord_manager.send_scan_results,
                current_user["user_id"],
                bot_info["channel_id"],
                scan_doc
            )
    
    logger.info(f"✅ Scan saved for user {current_user['username']}: {scan.name}")
    
    return {
        "success": True,
        "scan_id": scan_doc["scan_id"],
        "discord_sent": current_user["user_id"] in discord_manager.bots
    }

@app.get("/api/scans/history")
async def get_scan_history(
    limit: int = 50,
    current_user: dict = Depends(get_current_user)
):
    """Get user's scan history"""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    
    cursor = scans_collection.find(
        {"user_id": current_user["user_id"]},
        {"r6_accounts": 0, "steam_accounts": 0, "suspicious_files": 0}
    ).sort("created_at", -1).limit(limit)
    
    scans = await cursor.to_list(length=limit)
    
    for scan in scans:
        scan["_id"] = str(scan["_id"])
        scan["created_at"] = scan["created_at"].isoformat()
    
    return {"scans": scans}

@app.get("/api/scans/{scan_id}")
async def get_scan_details(
    scan_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get detailed scan results"""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    
    scan = await scans_collection.find_one({
        "scan_id": scan_id,
        "user_id": current_user["user_id"]
    })
    
    if not scan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scan not found"
        )
    
    scan["_id"] = str(scan["_id"])
    scan["created_at"] = scan["created_at"].isoformat()
    
    return scan

# Owner routes
@app.get("/api/admin/users")
async def get_all_users(current_owner: dict = Depends(get_current_owner)):
    """Get all users (owner only)"""
    cursor = users_collection.find({}, {"hashed_password": 0})
    users = await cursor.to_list(length=1000)
    
    for user in users:
        user["_id"] = str(user["_id"])
        user["created_at"] = user["created_at"].isoformat()
        if user.get("last_login"):
            user["last_login"] = user["last_login"].isoformat()
    
    return {"users": users}

@app.post("/api/admin/update-role")
async def update_user_role(
    role_update: RoleUpdate,
    current_owner: dict = Depends(get_current_owner)
):
    """Update user role (owner only)"""
    if role_update.username == "xotiic":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot modify primary owner role"
        )
    
    result = await users_collection.update_one(
        {"username": role_update.username},
        {"$set": {"role": role_update.role}}
    )
    
    if result.modified_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    logger.info(f"Role updated for {role_update.username} to {role_update.role}")
    
    return {"success": True, "message": f"Role updated for {role_update.username}"}

@app.on_event("startup")
async def startup_event():
    """Initialize on startup"""
    try:
        # Create indexes
        await users_collection.create_index("username", unique=True)
        await users_collection.create_index("email", unique=True)
        await users_collection.create_index("user_id", unique=True)
        await scans_collection.create_index("scan_id", unique=True)
        await scans_collection.create_index("user_id")
        await scans_collection.create_index("created_at")
        await tokens_collection.create_index("refresh_token", unique=True)
        await tokens_collection.create_index("expires_at")
        
        logger.info("✅ Database indexes created")
        
        # Create owner account if doesn't exist
        owner = await users_collection.find_one({"username": "xotiic"})
        
        if not owner:
            hashed_password = bcrypt.hashpw(b"40671Mps19*", bcrypt.gensalt())
            
            owner_doc = {
                "user_id": str(uuid.uuid4()),
                "username": "xotiic",
                "email": "owner@r6x-cyberscan.com",
                "hashed_password": hashed_password.decode('utf-8'),
                "role": "owner",
                "created_at": datetime.utcnow(),
                "last_login": None,
                "is_active": True,
                "total_scans": 0,
                "threats_found": 0
            }
            
            await users_collection.insert_one(owner_doc)
            logger.info("✅ Default owner account created")
        
        logger.info(f"🚀 R6X CYBERSCAN Backend v{config.API_VERSION} started")
        logger.info(f"💾 MongoDB: Connected")
        logger.info(f"🤖 Discord Manager: Active with {len(discord_manager.bots)} bots")
        
    except Exception as e:
        logger.error(f"Startup error: {e}")
        logger.error(traceback.format_exc())

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("Shutting down...")
    await discord_manager.disconnect_all_bots()
    logger.info("All Discord bots disconnected")

# For local testing
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(
        "backend:app",
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
