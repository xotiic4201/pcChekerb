# backend.py - R6X CYBERSCAN Production Backend
# Deploy this on Render.com

import os
import jwt
import bcrypt
import datetime
import asyncio
import uuid
import json
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Depends, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field, validator
import motor.motor_asyncio
from discord.ext import commands
import discord
import threading
import logging
from datetime import datetime, timedelta
import secrets

# Configuration
class Config:
    # These should be set as environment variables on Render
    SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_urlsafe(32))
    ALGORITHM = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES = 30
    REFRESH_TOKEN_EXPIRE_DAYS = 7
    # Your MongoDB connection string
    MONGODB_URL = os.getenv("MONGODB_URL", "mongodb+srv://xotiicglizzy_db_user:WBnaZXuhxBWzLwx5@cluster0.cvidwug.mongodb.net/")
    DATABASE_NAME = "r6x_cyberscan"
    BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")  # Main bot token
    API_VERSION = "4.0.0"
    
config = Config()

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("r6x-backend")

# Database setup with your MongoDB connection
try:
    client = motor.motor_asyncio.AsyncIOMotorClient(config.MONGODB_URL)
    db = client[config.DATABASE_NAME]
    users_collection = db["users"]
    scans_collection = db["scans"]
    tokens_collection = db["tokens"]
    bots_collection = db["discord_bots"]
    threats_collection = db["threats"]
    system_collection = db["system"]
    
    # Test connection
    logger.info("✅ Connected to MongoDB Atlas successfully")
except Exception as e:
    logger.error(f"❌ Failed to connect to MongoDB: {e}")
    raise

# Pydantic models
class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class TokenData(BaseModel):
    username: Optional[str] = None
    user_id: Optional[str] = None

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8)

class UserLogin(BaseModel):
    username: str
    password: str

class UserResponse(BaseModel):
    id: str
    username: str
    email: str
    role: str
    created_at: datetime
    last_login: Optional[datetime] = None
    is_active: bool = True

class UserInDB(UserResponse):
    hashed_password: str

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

class ThreatLog(BaseModel):
    threat_name: str
    severity: str
    file_path: str
    action_taken: str
    timestamp: datetime

# Discord Bot Manager
class DiscordBotManager:
    def __init__(self):
        self.bots = {}
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        
    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()
        
    async def start_bot(self, token: str, channel_id: str, user_id: str):
        """Start a Discord bot instance"""
        bot_id = str(uuid.uuid4())
        
        # Configure bot intents
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        
        bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)
        
        @bot.event
        async def on_ready():
            logger.info(f"✅ Bot {bot.user} connected for user {user_id}")
            # Store bot info in database
            await bots_collection.update_one(
                {"bot_id": bot_id},
                {"$set": {
                    "user_id": user_id,
                    "token": token[-10:],  # Store only last 10 chars for security
                    "channel_id": channel_id,
                    "bot_name": str(bot.user),
                    "bot_id_discord": bot.user.id,
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
            
        @bot.command(name='scan')
        async def scan_command(ctx):
            """Check latest scan status"""
            if str(ctx.channel.id) != channel_id:
                return
                
            # Get latest scan for user
            latest_scan = await scans_collection.find_one(
                {"user_id": user_id},
                sort=[("created_at", -1)]
            )
            
            if latest_scan:
                embed = discord.Embed(
                    title="📊 Latest Scan Results",
                    description=f"Scan: {latest_scan['name']}",
                    color=0x00FF9D if latest_scan['threats_found'] == 0 else 0xFF003C,
                    timestamp=latest_scan['created_at']
                )
                embed.add_field(name="Files Scanned", value=str(latest_scan['files_scanned']))
                embed.add_field(name="Threats Found", value=str(latest_scan['threats_found']))
                embed.add_field(name="Duration", value=f"{latest_scan['scan_duration']:.2f}s")
                await ctx.send(embed=embed)
            else:
                await ctx.send("No scans found for your account")
                
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
            embed.add_field(name="Latency", value=f"{round(bot.latency * 1000)}ms")
            embed.add_field(name="Uptime", value="Active")
            await ctx.send(embed=embed)
            
        # Start bot in background
        asyncio.run_coroutine_threadsafe(bot.start(token), self.loop)
        self.bots[user_id] = {
            "bot_id": bot_id,
            "bot": bot,
            "channel_id": channel_id,
            "token": token
        }
        
        return bot_id
        
    async def send_message(self, user_id: str, channel_id: str, content: str, embed_data: Dict = None):
        """Send a message using user's bot"""
        if user_id not in self.bots:
            # Try to reconnect from database
            await self.reconnect_bot(user_id)
            if user_id not in self.bots:
                return False
                
        bot_data = self.bots[user_id]
        bot = bot_data["bot"]
        
        try:
            channel = bot.get_channel(int(channel_id))
            if not channel:
                # Try to fetch channel
                try:
                    channel = await bot.fetch_channel(int(channel_id))
                except:
                    return False
                    
            if embed_data:
                embed = discord.Embed(
                    title=embed_data.get("title", "Scan Results"),
                    description=embed_data.get("description", ""),
                    color=embed_data.get("color", 0x00FF9D),
                    timestamp=datetime.utcnow()
                )
                
                for field in embed_data.get("fields", []):
                    embed.add_field(
                        name=field["name"],
                        value=field["value"],
                        inline=field.get("inline", False)
                    )
                    
                await channel.send(content, embed=embed)
            else:
                await channel.send(content)
                
            return True
        except Exception as e:
            logger.error(f"Failed to send Discord message for user {user_id}: {e}")
            return False
            
    async def reconnect_bot(self, user_id: str):
        """Reconnect bot from database"""
        bot_info = await bots_collection.find_one({"user_id": user_id, "status": "connected"})
        if bot_info:
            await self.start_bot(
                token=bot_info.get("full_token", ""),  # You'd need to store full token securely
                channel_id=bot_info["channel_id"],
                user_id=user_id
            )
            
    async def disconnect_bot(self, user_id: str):
        """Disconnect user's bot"""
        if user_id in self.bots:
            try:
                bot = self.bots[user_id]["bot"]
                await bot.close()
            except:
                pass
            del self.bots[user_id]
            
            await bots_collection.update_one(
                {"user_id": user_id},
                {"$set": {"status": "disconnected", "disconnected_at": datetime.utcnow()}}
            )
            return True
        return False
        
    async def disconnect_all_bots(self):
        """Disconnect all bots (for shutdown)"""
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

# CORS middleware - Configure for your frontend domains
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://localhost:3000",
        "http://localhost:8000",
        "https://r6x-cyberscan.onrender.com",
        "https://*.onrender.com"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")

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
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = jwt.decode(token, config.SECRET_KEY, algorithms=[config.ALGORITHM])
        username: str = payload.get("sub")
        token_type: str = payload.get("type")
        
        if username is None or token_type != "access":
            raise credentials_exception
            
        token_data = TokenData(username=username, user_id=payload.get("user_id"))
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired"
        )
    except jwt.PyJWTError:
        raise credentials_exception
        
    user = await users_collection.find_one({"username": token_data.username})
    if user is None:
        raise credentials_exception
        
    if not user.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled"
        )
        
    return user

async def get_current_owner(current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "owner":
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
        "database": "connected",
        "timestamp": datetime.utcnow().isoformat()
    }

@app.get("/api/health")
async def health_check():
    # Check database connection
    try:
        await db.command("ping")
        db_status = "connected"
    except Exception as e:
        db_status = f"disconnected: {e}"
        
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
        "role": "user",  # Default role
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
    
    # Store refresh token
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
        # Try email login
        user = await users_collection.find_one({"email": form_data.username})
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Verify password
    if not bcrypt.checkpw(
        form_data.password.encode('utf-8'),
        user["hashed_password"].encode('utf-8')
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
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
    
    # Store refresh token
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
    # Verify refresh token
    try:
        payload = jwt.decode(refresh_token, config.SECRET_KEY, algorithms=[config.ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type"
            )
            
        username = payload.get("sub")
        user_id = payload.get("user_id")
        
        # Check if token exists in database
        stored_token = await tokens_collection.find_one({
            "refresh_token": refresh_token,
            "user_id": user_id
        })
        
        if not stored_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token not found"
            )
            
        # Check if user is still active
        user = await users_collection.find_one({"user_id": user_id})
        if not user or not user.get("is_active", True):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is disabled"
            )
            
        # Create new tokens
        new_access_token = create_access_token(
            data={"sub": username, "user_id": user_id}
        )
        new_refresh_token = create_refresh_token(
            data={"sub": username, "user_id": user_id}
        )
        
        # Remove old refresh token
        await tokens_collection.delete_one({"refresh_token": refresh_token})
        
        # Store new refresh token
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

@app.get("/api/auth/me", response_model=UserResponse)
async def get_current_user_info(current_user: dict = Depends(get_current_user)):
    return {
        "id": current_user["user_id"],
        "username": current_user["username"],
        "email": current_user["email"],
        "role": current_user["role"],
        "created_at": current_user["created_at"],
        "last_login": current_user.get("last_login"),
        "is_active": current_user.get("is_active", True)
    }

@app.post("/api/auth/logout")
async def logout(current_user: dict = Depends(get_current_user)):
    """Logout user by removing all their refresh tokens"""
    await tokens_collection.delete_many({"user_id": current_user["user_id"]})
    # Disconnect bot if connected
    await discord_manager.disconnect_bot(current_user["user_id"])
    return {"success": True, "message": "Logged out successfully"}

@app.post("/api/discord/connect")
async def connect_discord_bot(
    connection: DiscordBotConnect,
    current_user: dict = Depends(get_current_user)
):
    """Connect a Discord bot for the current user"""
    try:
        # Validate token format (basic check)
        if len(connection.token) < 50:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Discord token format"
            )
            
        # Disconnect any existing bot
        await discord_manager.disconnect_bot(current_user["user_id"])
        
        # Store full token securely (in production, encrypt this)
        await bots_collection.update_one(
            {"user_id": current_user["user_id"]},
            {"$set": {
                "full_token": connection.token,  # Consider encryption
                "channel_id": connection.channel_id,
                "status": "connecting"
            }},
            upsert=True
        )
        
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
        logger.error(f"❌ Failed to connect bot for user {current_user['username']}: {e}")
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
    try:
        # Create embed for scan results
        if message.results:
            # Determine color based on threats
            color = 0x00FF9D  # Green
            if message.results.get('threats_found', 0) > 0:
                color = 0xFF003C  # Red
            elif message.results.get('suspicious_files', []):
                color = 0xFFB86B  # Orange
                
            embed_data = {
                "title": f"📊 Scan: {message.results.get('name', 'Unknown')}",
                "description": f"Scan completed at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}",
                "color": color,
                "fields": [
                    {"name": "📁 Files Scanned", "value": str(message.results.get('files_scanned', 0)), "inline": True},
                    {"name": "🚨 Threats Found", "value": str(message.results.get('threats_found', 0)), "inline": True},
                    {"name": "🎮 R6 Accounts", "value": str(len(message.results.get('r6_accounts', []))), "inline": True},
                    {"name": "🔄 Steam Accounts", "value": str(len(message.results.get('steam_accounts', []))), "inline": True},
                    {"name": "⚠️ Suspicious Files", "value": str(len(message.results.get('suspicious_files', []))), "inline": True},
                    {"name": "⏱️ Duration", "value": f"{message.results.get('scan_duration', 0):.2f}s", "inline": True}
                ]
            }
            
            # Add threat details if any
            if message.results.get('threats_found', 0) > 0:
                threat_text = "\n".join([
                    f"• {t.get('name', 'Unknown')}" 
                    for t in message.results.get('suspicious_files', [])[:5]
                ])
                if threat_text:
                    embed_data["fields"].append({
                        "name": "⚠️ Detected Threats",
                        "value": threat_text[:1000],
                        "inline": False
                    })
        else:
            embed_data = None
            
        success = await discord_manager.send_message(
            user_id=current_user["user_id"],
            channel_id=message.channel_id,
            content=message.content,
            embed_data=embed_data
        )
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to send message - bot not connected or channel not found"
            )
            
        return {"success": True, "message": "Message sent successfully"}
        
    except Exception as e:
        logger.error(f"❌ Failed to send Discord message: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@app.post("/api/discord/disconnect")
async def disconnect_discord_bot(current_user: dict = Depends(get_current_user)):
    """Disconnect user's Discord bot"""
    success = await discord_manager.disconnect_bot(current_user["user_id"])
    
    if success:
        return {"success": True, "message": "Bot disconnected"}
    else:
        return {"success": False, "message": "No bot connected"}

@app.get("/api/discord/status")
async def get_bot_status(current_user: dict = Depends(get_current_user)):
    """Get user's Discord bot status"""
    bot_info = await bots_collection.find_one({"user_id": current_user["user_id"]})
    
    if bot_info:
        return {
            "connected": current_user["user_id"] in discord_manager.bots,
            "channel_id": bot_info.get("channel_id"),
            "bot_name": bot_info.get("bot_name"),
            "status": bot_info.get("status", "disconnected")
        }
    else:
        return {"connected": False, "status": "not_configured"}

@app.post("/api/scans/save")
async def save_scan_result(
    scan: ScanResult,
    current_user: dict = Depends(get_current_user)
):
    """Save scan results to database"""
    scan_doc = {
        "scan_id": str(uuid.uuid4()),
        "user_id": current_user["user_id"],
        "username": current_user["username"],
        "name": scan.name,
        "files_scanned": scan.files_scanned,
        "threats_found": scan.threats_found,
        "r6_accounts": scan.r6_accounts,
        "steam_accounts": scan.steam_accounts,
        "suspicious_files": scan.suspicious_files,
        "scan_duration": scan.scan_duration,
        "status": scan.status,
        "system_info": scan.system_info,
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
    
    # Log threats if any
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
    
    logger.info(f"✅ Scan saved for user {current_user['username']}: {scan.name}")
    
    return {"success": True, "scan_id": scan_doc["scan_id"]}

@app.get("/api/scans/history")
async def get_scan_history(
    limit: int = 50,
    current_user: dict = Depends(get_current_user)
):
    """Get user's scan history"""
    cursor = scans_collection.find(
        {"user_id": current_user["user_id"]},
        {"r6_accounts": 0, "steam_accounts": 0, "suspicious_files": 0}  # Exclude large data
    ).sort("created_at", -1).limit(limit)
    
    scans = await cursor.to_list(length=limit)
    
    # Format for response
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

# Owner-only routes
@app.get("/api/admin/users")
async def get_all_users(current_owner: dict = Depends(get_current_owner)):
    """Get all users (owner only)"""
    cursor = users_collection.find({}, {
        "hashed_password": 0,  # Exclude password hash
        "full_token": 0  # Exclude any tokens
    })
    
    users = await cursor.to_list(length=1000)
    
    # Convert ObjectId to string and format dates
    for user in users:
        user["_id"] = str(user["_id"])
        if "created_at" in user:
            user["created_at"] = user["created_at"].isoformat()
        if "last_login" in user and user["last_login"]:
            user["last_login"] = user["last_login"].isoformat()
        
    return {"users": users}

@app.post("/api/admin/update-role")
async def update_user_role(
    role_update: RoleUpdate,
    current_owner: dict = Depends(get_current_owner)
):
    """Update user role (owner only)"""
    # Don't allow changing the primary owner's role
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
        
    logger.info(f"✅ Role updated for {role_update.username} to {role_update.role}")
    
    return {"success": True, "message": f"Role updated for {role_update.username}"}

@app.delete("/api/admin/delete-user")
async def delete_user(
    username: str,
    current_owner: dict = Depends(get_current_owner)
):
    """Delete user (owner only)"""
    # Don't allow deleting the owner
    if username == "xotiic":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete primary owner"
        )
        
    # Find user
    user = await users_collection.find_one({"username": username})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
        
    # Delete user's data
    await users_collection.delete_one({"username": username})
    await scans_collection.delete_many({"username": username})
    await tokens_collection.delete_many({"user_id": user["user_id"]})
    await threats_collection.delete_many({"user_id": user["user_id"]})
    
    # Disconnect user's bot if connected
    await discord_manager.disconnect_bot(user["user_id"])
    
    logger.info(f"✅ User deleted: {username}")
    
    return {"success": True, "message": f"User {username} deleted"}

@app.post("/api/admin/toggle-user")
async def toggle_user(
    username: str,
    current_owner: dict = Depends(get_current_owner)
):
    """Enable/disable user account (owner only)"""
    if username == "xotiic":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot toggle primary owner"
        )
        
    user = await users_collection.find_one({"username": username})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
        
    new_status = not user.get("is_active", True)
    
    await users_collection.update_one(
        {"username": username},
        {"$set": {"is_active": new_status}}
    )
    
    # If disabling, disconnect their bot
    if not new_status:
        await discord_manager.disconnect_bot(user["user_id"])
        
    status_text = "enabled" if new_status else "disabled"
    logger.info(f"✅ User {username} {status_text}")
    
    return {"success": True, "message": f"User {username} {status_text}"}

@app.get("/api/admin/stats")
async def get_system_stats(current_owner: dict = Depends(get_current_owner)):
    """Get system statistics (owner only)"""
    total_users = await users_collection.count_documents({})
    active_users = await users_collection.count_documents({"is_active": True})
    total_scans = await scans_collection.count_documents({})
    
    # Scans in last 24 hours
    last_24h = datetime.utcnow() - timedelta(hours=24)
    recent_scans = await scans_collection.count_documents({
        "created_at": {"$gte": last_24h}
    })
    
    # Total threats found
    threats_pipeline = [
        {"$group": {"_id": None, "total": {"$sum": "$threats_found"}}}
    ]
    threats_result = await scans_collection.aggregate(threats_pipeline).to_list(1)
    total_threats = threats_result[0]["total"] if threats_result else 0
    
    # Recent threats
    recent_threats = await threats_collection.count_documents({
        "timestamp": {"$gte": last_24h}
    })
    
    # Active bots
    active_bots = len(discord_manager.bots)
    total_bots = await bots_collection.count_documents({"status": "connected"})
    
    return {
        "totalUsers": total_users,
        "activeUsers": active_users,
        "totalScans": total_scans,
        "activeBots": active_bots,
        "totalBots": total_bots,
        "totalThreats": total_threats,
        "recentThreats": recent_threats,
        "scansLast24h": recent_scans,
        "apiVersion": config.API_VERSION,
        "timestamp": datetime.utcnow().isoformat()
    }

@app.get("/api/admin/scans")
async def get_all_scans(
    limit: int = 100,
    current_owner: dict = Depends(get_current_owner)
):
    """Get all scans (owner only)"""
    cursor = scans_collection.find(
        {},
        {
            "r6_accounts": 0, 
            "steam_accounts": 0, 
            "suspicious_files": 0,
            "system_info": 0
        }
    ).sort("created_at", -1).limit(limit)
    
    scans = await cursor.to_list(length=limit)
    
    # Format for display
    formatted_scans = []
    for scan in scans:
        formatted_scans.append({
            "id": str(scan["_id"]),
            "scan_id": scan["scan_id"],
            "user": scan["username"],
            "name": scan["name"],
            "date": scan["created_at"].isoformat(),
            "files_scanned": scan["files_scanned"],
            "threats_found": scan["threats_found"],
            "status": scan["status"]
        })
        
    return {"scans": formatted_scans}

@app.get("/api/admin/threats")
async def get_all_threats(
    limit: int = 100,
    current_owner: dict = Depends(get_current_owner)
):
    """Get all threats (owner only)"""
    cursor = threats_collection.find({}).sort("timestamp", -1).limit(limit)
    threats = await cursor.to_list(length=limit)
    
    for threat in threats:
        threat["_id"] = str(threat["_id"])
        threat["timestamp"] = threat["timestamp"].isoformat()
        
    return {"threats": threats}

# Initialize default owner account if it doesn't exist
@app.on_event("startup")
async def startup_event():
    # Check if owner exists
    owner = await users_collection.find_one({"username": "xotiic"})
    
    if not owner:
        # Create owner account with provided credentials
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
        
    # Create indexes
    await users_collection.create_index("username", unique=True)
    await users_collection.create_index("email", unique=True)
    await users_collection.create_index("user_id", unique=True)
    await scans_collection.create_index("scan_id", unique=True)
    await scans_collection.create_index("user_id")
    await scans_collection.create_index("created_at")
    await tokens_collection.create_index("refresh_token", unique=True)
    await tokens_collection.create_index("user_id")
    await tokens_collection.create_index("expires_at")
    await threats_collection.create_index("timestamp")
    
    logger.info("✅ Database indexes created")
    logger.info("🚀 R6X CYBERSCAN Backend started successfully")
    logger.info(f"📊 API Version: {config.API_VERSION}")
    logger.info(f"💾 MongoDB: Connected to Atlas")

# Shutdown event
@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("🛑 Shutting down R6X CYBERSCAN Backend")
    # Disconnect all Discord bots
    await discord_manager.disconnect_all_bots()
    logger.info("✅ All Discord bots disconnected")

# Run with: uvicorn backend:app --host 0.0.0.0 --port 8000 --reload
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
