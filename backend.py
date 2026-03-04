# app.py - R6X CYBERSCAN Backend with Supabase
# Single Discord bot for all users

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

# ========== CONFIGURATION ==========
class Config:
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
    SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
    SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_urlsafe(32))
    ALGORITHM = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES = 30
    REFRESH_TOKEN_EXPIRE_DAYS = 7
    
    # Single Discord bot for everyone
    DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")
    
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

# ========== DISCORD BOT (SINGLE INSTANCE) ==========
class DiscordBot:
    def __init__(self):
        self.bot = None
        self.loop = None
        self.thread = None
        self.channel = None
        self.is_ready = False
        
    def start(self):
        """Start the Discord bot in a separate thread"""
        self.thread = threading.Thread(target=self._run_bot, daemon=True)
        self.thread.start()
        
    def _run_bot(self):
        """Run the bot in its own event loop"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        try:
            self.loop.run_until_complete(self._setup_bot())
        except Exception as e:
            logger.error(f"Bot error: {e}")
        finally:
            self.loop.close()
            
    async def _setup_bot(self):
        """Setup and run the Discord bot"""
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        
        self.bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)
        
        @self.bot.event
        async def on_ready():
            logger.info(f"✅ Discord bot connected as {self.bot.user}")
            self.is_ready = True
            
            # Get the channel
            self.channel = self.bot.get_channel(int(config.DISCORD_CHANNEL_ID))
            if not self.channel:
                for guild in self.bot.guilds:
                    self.channel = guild.get_channel(int(config.DISCORD_CHANNEL_ID))
                    if self.channel:
                        break
            
            if self.channel:
                embed = discord.Embed(
                    title="🤖 R6X Bot Online",
                    description="Security scanner bot is now active!",
                    color=0x00FF9D,
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Status", value="🟢 Online", inline=True)
                embed.add_field(name="Commands", value="!scan [username], !status, !help", inline=True)
                embed.set_footer(text="R6X CyberScan")
                
                await self.channel.send(embed=embed)
        
        @self.bot.event
        async def on_command_error(ctx, error):
            if isinstance(error, commands.CommandNotFound):
                return
            await ctx.send(f"❌ Error: {str(error)}")
        
        @self.bot.command(name='scan')
        async def scan_command(ctx, username: str = None):
            """Get latest scan results for a user"""
            if not username:
                await ctx.send("❌ Please specify a username: `!scan username`")
                return
            
            # Get latest scan from database
            result = supabase.table("scans")\
                .select("*")\
                .eq("username", username)\
                .order("created_at", desc=True)\
                .limit(1)\
                .execute()
            
            if result.data and len(result.data) > 0:
                scan = result.data[0]
                color = 0x00FF9D if scan['threats_found'] == 0 else 0xFF003C
                
                embed = discord.Embed(
                    title=f"📊 Scan Results: {username}",
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
                embed.add_field(name="Computer", value=scan['computer'], inline=True)
                
                await ctx.send(embed=embed)
            else:
                await ctx.send(f"📭 No scans found for user: {username}")
        
        @self.bot.command(name='status')
        async def status_command(ctx):
            """Check bot status"""
            total_users = supabase.table("users").select("*", count="exact").execute()
            total_scans = supabase.table("scans").select("*", count="exact").execute()
            
            embed = discord.Embed(
                title="🟢 Bot Status",
                description="R6X CyberScan Bot is operational",
                color=0x00FF9D
            )
            embed.add_field(name="Latency", value=f"{round(self.bot.latency * 1000)}ms", inline=True)
            embed.add_field(name="Total Users", value=str(total_users.count if hasattr(total_users, 'count') else 0), inline=True)
            embed.add_field(name="Total Scans", value=str(total_scans.count if hasattr(total_scans, 'count') else 0), inline=True)
            
            await ctx.send(embed=embed)
        
        @self.bot.command(name='help')
        async def help_command(ctx):
            """Show available commands"""
            embed = discord.Embed(
                title="🤖 R6X Bot Commands",
                description="Available commands",
                color=0x5865F2
            )
            embed.add_field(name="!scan [username]", value="Get latest scan for a user", inline=False)
            embed.add_field(name="!status", value="Check bot status", inline=False)
            embed.add_field(name="!help", value="Show this message", inline=False)
            embed.set_footer(text="R6X CyberScan v4.0")
            
            await ctx.send(embed=embed)
        
        # Start the bot
        await self.bot.start(config.DISCORD_BOT_TOKEN)
    
    async def send_scan_results(self, scan_data: Dict):
        """Send scan results to Discord channel"""
        if not self.is_ready or not self.channel:
            return False
            
        try:
            # Create main embed
            color = 0x00FF9D if scan_data['threats_found'] == 0 else 0xFF003C
            embed = discord.Embed(
                title=f"📊 New Scan: {scan_data['username']}",
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
            
            await self.channel.send(embed=embed)
            
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
                    await self.channel.send(embed=sus_embed)
            
            return True
        except Exception as e:
            logger.error(f"Error sending to Discord: {e}")
            return False

# Initialize Discord bot
discord_bot = DiscordBot()

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

# ========== API ROUTES ==========

@app.get("/")
async def root():
    return {
        "service": "R6X CYBERSCAN API",
        "status": "online",
        "bot_status": "connected" if discord_bot.is_ready else "starting",
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
        "total_scans": current_user.get("total_scans", 0),
        "threats_found": current_user.get("threats_found", 0),
        "created_at": current_user.get("created_at")
    }

@app.post("/api/auth/logout")
async def logout(current_user: dict = Depends(get_current_user)):
    if current_user:
        service_supabase.table("tokens").delete().eq("user_id", current_user["user_id"]).execute()
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

# ========== SCAN ROUTES ==========

@app.post("/api/scan/save")
async def save_scan(
    scan: ScanResult,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Save scan results and send to Discord"""
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
    
    # Send to Discord in background
    background_tasks.add_task(discord_bot.send_scan_results, scan.dict())
    
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
async def get_users(current_user: dict = Depends(get_current_user)):
    """Get all users (admin only - username 'xotiic')"""
    if not current_user or current_user.get("username") != "xotiic":
        raise HTTPException(403, "Admin only")
    
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
async def get_stats(current_user: dict = Depends(get_current_user)):
    """Get system stats (admin only - username 'xotiic')"""
    if not current_user or current_user.get("username") != "xotiic":
        raise HTTPException(403, "Admin only")
    
    users_result = supabase.table("users").select("*", count="exact").execute()
    scans_result = supabase.table("scans").select("*", count="exact").execute()
    
    return {
        "total_users": users_result.count if hasattr(users_result, 'count') else 0,
        "total_scans": scans_result.count if hasattr(scans_result, 'count') else 0,
        "bot_status": "connected" if discord_bot.is_ready else "starting"
    }

# ========== STARTUP ==========
@app.on_event("startup")
async def startup():
    logger.info("🚀 R6X CYBERSCAN Backend starting...")
    
    # Start Discord bot
    if config.DISCORD_BOT_TOKEN and config.DISCORD_CHANNEL_ID:
        discord_bot.start()
        logger.info("✅ Discord bot starting...")
    else:
        logger.warning("⚠️ Discord bot not configured - check environment variables")
    
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
    """Cleanup on shutdown"""
    logger.info("Shutting down...")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
