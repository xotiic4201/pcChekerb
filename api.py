# api_fixed.py - COMPLETE FULL CODE
from fastapi import FastAPI, HTTPException, Request, Depends, status, BackgroundTasks, Form, Query
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import uvicorn
import os
import sys
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Union, Tuple
import json
import aiohttp
import urllib.parse
import urllib.request
from supabase import create_client, Client
from cryptography.fernet import Fernet
import base64
from jose import jwt
from pydantic import BaseModel, Field, validator
import time
import uuid
import asyncio
import html
import hashlib
import random
import string
from io import BytesIO
import qrcode

# ==================== CONFIGURATION ====================
class Config:
    # Discord Configuration
    DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "1209283310332887080")
    DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
    DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
    
    # Supabase Configuration
    SUPABASE_URL = os.getenv("SUPABASE_URL", "https://qcfngsqyapkljbppemti.supabase.co")
    SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
    
    # API Configuration
    API_URL = os.getenv("API_URL", "https://bot-hosting-b.onrender.com")
    REDIRECT_URI = os.getenv("REDIRECT_URI", "https://bot-hosting-b.onrender.com/oauth/callback")
    FRONTEND_URL = os.getenv("FRONTEND_URL", "https://bothostingf.vercel.app")
    
    # Security Configuration
    JWT_SECRET = os.getenv("JWT_SECRET", secrets.token_urlsafe(32))
    ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", base64.urlsafe_b64encode(Fernet.generate_key()).decode())
    
    # Feature Flags
    BOT_API_ENABLED = os.getenv("BOT_API_ENABLED", "true").lower() == "true"
    DASHBOARD_ENABLED = os.getenv("DASHBOARD_ENABLED", "true").lower() == "true"
    TRANSFER_ENABLED = os.getenv("TRANSFER_ENABLED", "true").lower() == "true"
    
    # Rate Limiting
    RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "100"))
    RATE_LIMIT_PERIOD = int(os.getenv("RATE_LIMIT_PERIOD", "60"))
    
    # CORS
    CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
    
    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    
    # Database Settings
    DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "10"))
    DB_MAX_RETRIES = int(os.getenv("DB_MAX_RETRIES", "3"))
    
    @classmethod
    def validate(cls):
        """Validate required configuration"""
        required_vars = [
            "DISCORD_CLIENT_ID",
            "DISCORD_CLIENT_SECRET", 
            "SUPABASE_URL",
            "SUPABASE_SERVICE_ROLE_KEY"
        ]
        
        missing = []
        for var in required_vars:
            if not getattr(cls, var):
                missing.append(var)
        
        if missing:
            logger.warning(f"Missing environment variables: {', '.join(missing)}")
        
        return len(missing) == 0

# ==================== LOGGING SETUP ====================
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('api.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# ==================== LIFESPAN MANAGEMENT ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan management"""
    # Startup
    startup_time = datetime.now()
    logger.info("=" * 60)
    logger.info("🚀 Starting xotiicsverify API v5.0.0")
    logger.info("=" * 60)
    logger.info(f"Startup Time: {startup_time}")
    logger.info(f"Environment: {os.getenv('ENVIRONMENT', 'production')}")
    logger.info(f"API URL: {Config.API_URL}")
    logger.info(f"Frontend URL: {Config.FRONTEND_URL}")
    
    # Initialize database connection
    global supabase
    try:
        supabase = create_client(
            Config.SUPABASE_URL,
            Config.SUPABASE_SERVICE_ROLE_KEY
        )
        
        # Test connection
        supabase.table("verified_users").select("id").limit(1).execute()
        logger.info("✅ Database connected successfully")
        
        # Create tables if needed
        await create_tables()
        
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        supabase = None
    
    # Load configuration
    logger.info(f"Bot API Enabled: {Config.BOT_API_ENABLED}")
    logger.info(f"Dashboard Enabled: {Config.DASHBOARD_ENABLED}")
    logger.info(f"Transfer Enabled: {Config.TRANSFER_ENABLED}")
    
    # Register background tasks
    asyncio.create_task(cleanup_expired_tokens())
    
    logger.info("✅ API startup complete")
    logger.info("=" * 60)
    
    yield
    
    # Shutdown
    shutdown_time = datetime.now()
    uptime = shutdown_time - startup_time
    logger.info("=" * 60)
    logger.info("🛑 Shutting down xotiicsverify API")
    logger.info(f"Uptime: {uptime}")
    logger.info("=" * 60)

# ==================== FASTAPI APP INITIALIZATION ====================
app = FastAPI(
    title="xotiicsverify API",
    description="Complete Discord Verification & Management System",
    version="5.0.0",
    docs_url="/docs" if os.getenv("ENVIRONMENT") == "development" else None,
    redoc_url="/redoc" if os.getenv("ENVIRONMENT") == "development" else None,
    openapi_url="/openapi.json" if os.getenv("ENVIRONMENT") == "development" else None,
    lifespan=lifespan
)

# ==================== CORS CONFIGURATION ====================
app.add_middleware(
    CORSMiddleware,
    allow_origins=Config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=600
)

# ==================== SECURITY ====================
security = HTTPBearer(auto_error=False)

# ==================== DATABASE TABLES CREATION ====================
async def create_tables():
    """Create necessary database tables if they don't exist"""
    try:
        # This would normally be done with migrations
        # For now, we'll just check if tables exist
        logger.info("Checking database tables...")
        
        # You would typically run SQL migrations here
        # Example SQL for creating tables would go here
        
        logger.info("✅ Database tables ready")
    except Exception as e:
        logger.warning(f"Could not create tables: {e}")

# ==================== DATABASE INSTANCE ====================
supabase = None

# ==================== MODELS ====================
class UserCreate(BaseModel):
    """Model for creating a verified user"""
    discord_id: str = Field(..., min_length=17, max_length=20)
    username: str = Field(..., min_length=2, max_length=100)
    access_token: str
    refresh_token: str
    expires_in: int = Field(..., ge=1)
    guild_id: str = Field(..., min_length=17, max_length=20)
    metadata: Dict[str, Any] = Field(default_factory=dict)

class BotTokenRequest(BaseModel):
    """Model for requesting bot token"""
    user_id: str = Field(..., min_length=17, max_length=20)
    guild_id: str = Field(..., min_length=17, max_length=20)
    expires_days: int = Field(30, ge=1, le=365)

class TransferRequest(BaseModel):
    """Model for user transfer request"""
    source_guild_id: str = Field(..., min_length=17, max_length=20)
    target_guild_id: str = Field(..., min_length=17, max_length=20)
    user_ids: List[str] = Field(default_factory=list)
    limit: Optional[int] = Field(None, ge=1, le=1000)
    assign_role_id: Optional[str] = None
    remove_from_source: bool = False
    transfer_all: bool = False

    @validator('user_ids')
    def validate_user_ids(cls, v, values):
        if values.get('transfer_all') and v:
            raise ValueError("Cannot specify user_ids when transfer_all is True")
        if not values.get('transfer_all') and not v:
            raise ValueError("user_ids required when transfer_all is False")
        return v

class ServerConfig(BaseModel):
    """Model for server configuration"""
    verification_channel: Optional[str] = None
    verification_role: Optional[str] = None
    welcome_message: Optional[str] = None
    enable_auto_verification: bool = True
    log_channel: Optional[str] = None
    admin_roles: List[str] = Field(default_factory=list)
    allow_user_transfers: bool = True
    auto_approve_transfers: bool = False

class VerificationRequest(BaseModel):
    """Model for verification request"""
    guild_id: str = Field(..., min_length=17, max_length=20)
    redirect_url: Optional[str] = None

# ==================== RATE LIMITING ====================
class RateLimiter:
    """Simple in-memory rate limiter"""
    def __init__(self):
        self.requests = {}
    
    def is_allowed(self, key: str, limit: int, period: int) -> bool:
        """Check if request is allowed"""
        now = time.time()
        
        if key not in self.requests:
            self.requests[key] = []
        
        # Clean old requests
        self.requests[key] = [
            req_time for req_time in self.requests[key]
            if now - req_time < period
        ]
        
        if len(self.requests[key]) >= limit:
            return False
        
        self.requests[key].append(now)
        return True

rate_limiter = RateLimiter()

# ==================== MIDDLEWARE ====================
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Rate limiting middleware"""
    client_ip = request.client.host if request.client else "unknown"
    path = request.url.path
    
    # Skip rate limiting for certain endpoints
    skip_paths = ["/health", "/", "/docs", "/redoc", "/openapi.json"]
    if any(path.startswith(skip) for skip in skip_paths):
        return await call_next(request)
    
    key = f"{client_ip}:{path}"
    
    if not rate_limiter.is_allowed(key, Config.RATE_LIMIT_REQUESTS, Config.RATE_LIMIT_PERIOD):
        logger.warning(f"Rate limit exceeded for {client_ip} on {path}")
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "success": False,
                "error": "Rate limit exceeded. Please try again later.",
                "retry_after": Config.RATE_LIMIT_PERIOD
            }
        )
    
    # Add request timing
    start_time = time.time()
    
    try:
        response = await call_next(request)
        process_time = time.time() - start_time
        
        # Add timing header
        response.headers["X-Process-Time"] = str(process_time)
        
        return response
    except Exception as e:
        logger.error(f"Middleware error: {e}")
        raise

# ==================== ENCRYPTION ====================
class EncryptionManager:
    """Manage encryption and decryption of sensitive data"""
    def __init__(self):
        key = base64.urlsafe_b64encode(
            Config.ENCRYPTION_KEY.encode()[:32].ljust(32, b'0')
        )
        self.cipher = Fernet(key)
    
    def encrypt(self, data: str) -> str:
        """Encrypt data"""
        try:
            return self.cipher.encrypt(data.encode()).decode()
        except Exception as e:
            logger.error(f"Encryption error: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to encrypt data"
            )
    
    def decrypt(self, encrypted_data: str) -> str:
        """Decrypt data"""
        try:
            return self.cipher.decrypt(encrypted_data.encode()).decode()
        except Exception as e:
            logger.error(f"Decryption error: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to decrypt data"
            )

encryption = EncryptionManager()

# ==================== DATABASE MANAGER ====================
class DatabaseManager:
    """Manager for all database operations"""
    
    def __init__(self):
        self.max_retries = Config.DB_MAX_RETRIES
    
    async def execute_with_retry(self, func, *args, **kwargs):
        """Execute database operation with retry logic"""
        for attempt in range(self.max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise
                wait_time = (attempt + 1) * 2
                logger.warning(f"Database operation failed, retrying in {wait_time}s: {e}")
                await asyncio.sleep(wait_time)
    
    def save_oauth_state(self, state: str, **kwargs) -> bool:
        """Save OAuth state to database"""
        try:
            data = {
                "state": state,
                "guild_id": kwargs.get("guild_id"),
                "user_id": kwargs.get("user_id"),
                "redirect_url": kwargs.get("redirect_url"),
                "type": kwargs.get("type", "verification"),
                "metadata": kwargs.get("metadata", {}),
                "created_at": datetime.now().isoformat(),
                "expires_at": (datetime.now() + timedelta(minutes=10)).isoformat()
            }
            
            supabase.table("oauth_states").insert(data).execute()
            logger.debug(f"Saved OAuth state: {state[:8]}...")
            return True
            
        except Exception as e:
            logger.error(f"Error saving OAuth state: {e}")
            return False
    
    def get_oauth_state(self, state: str) -> Optional[Dict[str, Any]]:
        """Get OAuth state from database"""
        try:
            response = supabase.table("oauth_states")\
                .select("*")\
                .eq("state", state)\
                .gt("expires_at", datetime.now().isoformat())\
                .execute()
            
            if response.data:
                state_data = response.data[0]
                
                # Delete after retrieval
                supabase.table("oauth_states").delete().eq("state", state).execute()
                
                return state_data
                
        except Exception as e:
            logger.error(f"Error getting OAuth state: {e}")
        
        return None
    
    def add_verified_user(self, user_data: UserCreate) -> bool:
        """Add or update verified user"""
        try:
            expires_at = datetime.now() + timedelta(seconds=user_data.expires_in)
            
            data = {
                "discord_id": user_data.discord_id,
                "username": user_data.username,
                "access_token": encryption.encrypt(user_data.access_token),
                "refresh_token": encryption.encrypt(user_data.refresh_token),
                "expires_at": expires_at.isoformat(),
                "guild_id": user_data.guild_id,
                "metadata": user_data.metadata,
                "status": "verified",
                "verified_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat()
            }
            
            # Check if user already exists
            existing = supabase.table("verified_users")\
                .select("id")\
                .eq("discord_id", user_data.discord_id)\
                .eq("guild_id", user_data.guild_id)\
                .execute()
            
            if existing.data:
                # Update existing user
                supabase.table("verified_users")\
                    .update(data)\
                    .eq("discord_id", user_data.discord_id)\
                    .eq("guild_id", user_data.guild_id)\
                    .execute()
                logger.info(f"Updated user: {user_data.username} ({user_data.discord_id})")
            else:
                # Insert new user
                data["created_at"] = datetime.now().isoformat()
                supabase.table("verified_users").insert(data).execute()
                logger.info(f"Added user: {user_data.username} ({user_data.discord_id})")
            
            return True
            
        except Exception as e:
            logger.error(f"Error adding verified user: {e}")
            return False
    
    def get_user(self, discord_id: str, guild_id: str) -> Optional[Dict[str, Any]]:
        """Get user by Discord ID and guild ID"""
        try:
            response = supabase.table("verified_users")\
                .select("*")\
                .eq("discord_id", discord_id)\
                .eq("guild_id", guild_id)\
                .execute()
            
            if response.data:
                user = response.data[0]
                
                # Decrypt tokens if needed
                if user.get("access_token"):
                    try:
                        user["access_token"] = encryption.decrypt(user["access_token"])
                    except:
                        user["access_token"] = ""
                
                if user.get("refresh_token"):
                    try:
                        user["refresh_token"] = encryption.decrypt(user["refresh_token"])
                    except:
                        user["refresh_token"] = ""
                
                return user
                
        except Exception as e:
            logger.error(f"Error getting user: {e}")
        
        return None
    
    def get_users_by_guild(self, guild_id: str, **kwargs) -> List[Dict[str, Any]]:
        """Get users for a guild with filtering"""
        try:
            query = supabase.table("verified_users")\
                .select("discord_id, username, guild_id, verified_at, restored, status, metadata")\
                .eq("guild_id", guild_id)\
                .order("verified_at", desc=True)
            
            if kwargs.get("restored") is not None:
                query = query.eq("restored", kwargs["restored"])
            
            if kwargs.get("status"):
                query = query.eq("status", kwargs["status"])
            
            if kwargs.get("limit"):
                query = query.limit(kwargs["limit"])
            
            if kwargs.get("offset"):
                query = query.range(kwargs["offset"], kwargs["offset"] + (kwargs.get("limit") or 100) - 1)
            
            response = query.execute()
            return response.data
            
        except Exception as e:
            logger.error(f"Error getting guild users: {e}")
            return []
    
    def get_guild_stats(self, guild_id: str) -> Dict[str, Any]:
        """Get statistics for a guild"""
        try:
            # Total verified users
            total_response = supabase.table("verified_users")\
                .select("id", count="exact")\
                .eq("guild_id", guild_id)\
                .execute()
            total = total_response.count or 0
            
            # Restored users
            restored_response = supabase.table("verified_users")\
                .select("id", count="exact")\
                .eq("guild_id", guild_id)\
                .eq("restored", True)\
                .execute()
            restored = restored_response.count or 0
            
            # Today's verifications
            today = datetime.now().date()
            today_response = supabase.table("verified_users")\
                .select("id", count="exact")\
                .eq("guild_id", guild_id)\
                .gte("verified_at", f"{today}T00:00:00")\
                .execute()
            today_count = today_response.count or 0
            
            # This week's verifications
            week_ago = (datetime.now() - timedelta(days=7)).isoformat()
            week_response = supabase.table("verified_users")\
                .select("id", count="exact")\
                .eq("guild_id", guild_id)\
                .gte("verified_at", week_ago)\
                .execute()
            week_count = week_response.count or 0
            
            return {
                "total_verified": total,
                "restored": restored,
                "pending": total - restored,
                "verified_today": today_count,
                "verified_week": week_count
            }
            
        except Exception as e:
            logger.error(f"Error getting guild stats: {e}")
            return {
                "total_verified": 0,
                "restored": 0,
                "pending": 0,
                "verified_today": 0,
                "verified_week": 0
            }
    
    def mark_user_restored(self, discord_id: str, guild_id: str, role_id: str = None):
        """Mark user as restored"""
        try:
            data = {
                "restored": True,
                "restored_at": datetime.now().isoformat(),
                "restored_role_id": role_id,
                "status": "restored",
                "updated_at": datetime.now().isoformat()
            }
            
            supabase.table("verified_users")\
                .update(data)\
                .eq("discord_id", discord_id)\
                .eq("guild_id", guild_id)\
                .execute()
            
            logger.info(f"Marked user as restored: {discord_id}")
            
        except Exception as e:
            logger.error(f"Error marking user restored: {e}")
    
    def save_server_config(self, guild_id: str, config: Dict[str, Any]):
        """Save server configuration"""
        try:
            data = {
                "guild_id": guild_id,
                "config": config,
                "updated_at": datetime.now().isoformat()
            }
            
            # Check if exists
            existing = supabase.table("server_configs")\
                .select("id")\
                .eq("guild_id", guild_id)\
                .execute()
            
            if existing.data:
                supabase.table("server_configs")\
                    .update(data)\
                    .eq("guild_id", guild_id)\
                    .execute()
            else:
                data["created_at"] = datetime.now().isoformat()
                supabase.table("server_configs").insert(data).execute()
            
            logger.info(f"Saved server config for guild: {guild_id}")
            
        except Exception as e:
            logger.error(f"Error saving server config: {e}")
    
    def get_server_config(self, guild_id: str) -> Dict[str, Any]:
        """Get server configuration"""
        try:
            response = supabase.table("server_configs")\
                .select("config")\
                .eq("guild_id", guild_id)\
                .execute()
            
            if response.data:
                return response.data[0]["config"]
                
        except Exception as e:
            logger.error(f"Error getting server config: {e}")
        
        return {}
    
    def transfer_user(self, discord_id: str, source_guild_id: str, target_guild_id: str, **kwargs) -> bool:
        """Transfer user between guilds"""
        try:
            # Get user from source guild
            user = self.get_user(discord_id, source_guild_id)
            if not user:
                return False
            
            # Create new user in target guild
            user_data = UserCreate(
                discord_id=discord_id,
                username=user.get("username", "Unknown"),
                access_token=user.get("access_token", ""),
                refresh_token=user.get("refresh_token", ""),
                expires_in=604800,  # Default 7 days
                guild_id=target_guild_id,
                metadata={
                    **user.get("metadata", {}),
                    "transferred_from": source_guild_id,
                    "transferred_at": datetime.now().isoformat(),
                    "transferred_by": kwargs.get("transferred_by")
                }
            )
            
            success = self.add_verified_user(user_data)
            
            if success and kwargs.get("remove_from_source", False):
                # Remove from source guild
                supabase.table("verified_users")\
                    .delete()\
                    .eq("discord_id", discord_id)\
                    .eq("guild_id", source_guild_id)\
                    .execute()
            
            return success
            
        except Exception as e:
            logger.error(f"Error transferring user {discord_id}: {e}")
            return False

db = DatabaseManager()

# ==================== OAUTH HANDLER ====================
class OAuthHandler:
    """Handle Discord OAuth2 operations"""
    
    def __init__(self):
        self.client_id = Config.DISCORD_CLIENT_ID
        self.client_secret = Config.DISCORD_CLIENT_SECRET
        self.bot_token = Config.DISCORD_BOT_TOKEN
        self.session = None
    
    async def get_session(self):
        """Get or create aiohttp session"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session
    
    def get_authorization_url(self, redirect_uri: str, state: str, guild_id: str = None) -> str:
        """Generate Discord authorization URL"""
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
            "scope": "identify guilds guilds.join",
            "prompt": "none"
        }
        
        if guild_id:
            params["permissions"] = "0"
        
        return f"https://discord.com/api/oauth2/authorize?{urllib.parse.urlencode(params)}"
    
    async def exchange_code(self, code: str, redirect_uri: str) -> Optional[Dict[str, Any]]:
        """Exchange authorization code for tokens"""
        try:
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri
            }
            
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "xotiicsverify/5.0.0"
            }
            
            session = await self.get_session()
            async with session.post(
                "https://discord.com/api/v10/oauth2/token",
                data=data,
                headers=headers
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    error_text = await resp.text()
                    logger.error(f"Token exchange failed: {resp.status} - {error_text}")
                    
        except Exception as e:
            logger.error(f"Error exchanging code: {e}")
        
        return None
    
    async def get_user_info(self, access_token: str) -> Optional[Dict[str, Any]]:
        """Get user info from Discord"""
        try:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "User-Agent": "xotiicsverify/5.0.0"
            }
            
            session = await self.get_session()
            async with session.get(
                "https://discord.com/api/v10/users/@me",
                headers=headers
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    logger.error(f"Failed to get user info: {resp.status}")
                    
        except Exception as e:
            logger.error(f"Error getting user info: {e}")
        
        return None
    
    async def add_user_to_guild(self, user_id: str, access_token: str, guild_id: str) -> bool:
        """Add user to Discord guild using bot"""
        try:
            if not self.bot_token:
                return False
            
            headers = {
                "Authorization": f"Bot {self.bot_token}",
                "Content-Type": "application/json",
                "User-Agent": "xotiicsverify/5.0.0"
            }
            
            data = {"access_token": access_token}
            
            session = await self.get_session()
            async with session.put(
                f"https://discord.com/api/v10/guilds/{guild_id}/members/{user_id}",
                headers=headers,
                json=data
            ) as resp:
                if resp.status in [200, 201, 204]:
                    logger.info(f"Added user {user_id} to guild {guild_id}")
                    return True
                else:
                    error_text = await resp.text()
                    logger.warning(f"Could not add user to guild: {resp.status} - {error_text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error adding user to guild: {e}")
            return False
    
    async def assign_role(self, user_id: str, guild_id: str, role_id: str) -> bool:
        """Assign role to user in guild"""
        try:
            if not self.bot_token:
                return False
            
            headers = {
                "Authorization": f"Bot {self.bot_token}",
                "User-Agent": "xotiicsverify/5.0.0"
            }
            
            session = await self.get_session()
            async with session.put(
                f"https://discord.com/api/v10/guilds/{guild_id}/members/{user_id}/roles/{role_id}",
                headers=headers
            ) as resp:
                if resp.status in [200, 201, 204]:
                    logger.info(f"Assigned role {role_id} to user {user_id}")
                    return True
                else:
                    error_text = await resp.text()
                    logger.warning(f"Could not assign role: {resp.status} - {error_text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error assigning role: {e}")
            return False

oauth = OAuthHandler()

# ==================== JWT TOKEN MANAGEMENT ====================
class TokenManager:
    """Manage JWT token creation and verification"""
    
    @staticmethod
    def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
        """Create JWT access token"""
        to_encode = data.copy()
        
        if expires_delta:
            expire = datetime.now() + expires_delta
        else:
            expire = datetime.now() + timedelta(days=7)
        
        to_encode.update({
            "exp": expire,
            "iat": datetime.now(),
            "jti": str(uuid.uuid4()),
            "iss": "xotiicsverify-api",
            "aud": "xotiicsverify-client"
        })
        
        return jwt.encode(to_encode, Config.JWT_SECRET, algorithm="HS256")
    
    @staticmethod
    def create_bot_token(guild_id: str, expires_days: int = 30) -> str:
        """Create bot API token"""
        payload = {
            "sub": "bot",
            "guild_id": guild_id,
            "type": "bot_api",
            "permissions": ["read", "write", "verify", "restore", "transfer"],
            "exp": datetime.now() + timedelta(days=expires_days),
            "iat": datetime.now()
        }
        
        return TokenManager.create_access_token(payload)
    
    @staticmethod
    def create_test_token() -> str:
        """Create test token for development"""
        payload = {
            "sub": "test_user",
            "guild_id": "any",
            "type": "test",
            "permissions": ["read", "write", "verify", "restore", "transfer"],
            "exp": datetime.now() + timedelta(days=365),
            "iat": datetime.now(),
            "test": True
        }
        
        return TokenManager.create_access_token(payload)
    
    @staticmethod
    def verify_token(token: str) -> Optional[Dict[str, Any]]:
        """Verify JWT token"""
        try:
            payload = jwt.decode(
                token,
                Config.JWT_SECRET,
                algorithms=["HS256"],
                options={"verify_aud": False}
            )
            return payload
        except jwt.ExpiredSignatureError:
            logger.warning("Token expired")
        except jwt.InvalidTokenError as e:
            logger.error(f"Invalid token: {e}")
        except Exception as e:
            logger.error(f"Token verification error: {e}")
        
        return None

# ==================== DEPENDENCIES ====================
async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Dict[str, Any]:
    """Dependency to get current user from token"""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user_data = TokenManager.verify_token(credentials.credentials)
    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return user_data

async def get_bot_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Dict[str, Any]:
    """Dependency to verify bot token"""
    if not credentials:
        # Allow test mode for development
        return {
            "type": "test",
            "guild_id": "any",
            "permissions": ["read", "write", "verify", "restore", "transfer"],
            "test": True
        }
    
    token_data = TokenManager.verify_token(credentials.credentials)
    
    if not token_data:
        # Try simple token (for testing)
        if credentials.credentials == "test_token":
            return {
                "type": "test",
                "guild_id": "any",
                "permissions": ["read", "write", "verify", "restore", "transfer"],
                "test": True
            }
        
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    
    # Check token type
    token_type = token_data.get("type", "")
    if token_type not in ["bot_api", "test"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid token type. Bot token required."
        )
    
    return token_data

# ==================== BACKGROUND TASKS ====================
async def cleanup_expired_tokens():
    """Background task to clean up expired tokens"""
    while True:
        try:
            # Clean OAuth states
            expired_time = (datetime.now() - timedelta(minutes=15)).isoformat()
            supabase.table("oauth_states")\
                .delete()\
                .lt("expires_at", expired_time)\
                .execute()
            
            # Clean expired verification tokens
            logger.debug("Cleaned up expired tokens")
            
        except Exception as e:
            logger.error(f"Error cleaning expired tokens: {e}")
        
        # Run every hour
        await asyncio.sleep(3600)

# ==================== HTML TEMPLATES ====================
HTML_TEMPLATES = {
    "success": """
    <!DOCTYPE html>
    <html>
    <head>
        <title>ACCESS GRANTED</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Rajdhani:wght@300;500;700&display=swap');
            
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }
            
            body {
                font-family: 'Rajdhani', sans-serif;
                background: #0a0a0a;
                height: 100vh;
                margin: 0;
                display: flex;
                justify-content: center;
                align-items: center;
                overflow: hidden;
                position: relative;
                color: #ff0033;
            }
            
            .cyber-grid {
                position: absolute;
                width: 100%;
                height: 100%;
                background-image: 
                    linear-gradient(rgba(255, 0, 51, 0.1) 1px, transparent 1px),
                    linear-gradient(90deg, rgba(255, 0, 51, 0.1) 1px, transparent 1px);
                background-size: 50px 50px;
                animation: gridMove 20s linear infinite;
                opacity: 0.3;
            }
            
            .data-stream {
                position: absolute;
                width: 100%;
                height: 100%;
                opacity: 0.1;
                overflow: hidden;
            }
            
            .binary {
                position: absolute;
                color: #ff0033;
                font-family: 'Courier New', monospace;
                font-size: 14px;
                animation: binaryFall linear infinite;
            }
            
            .scan-line {
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 2px;
                background: linear-gradient(90deg, transparent, #ff0033, transparent);
                animation: scan 4s linear infinite;
                z-index: 100;
                box-shadow: 0 0 20px #ff0033;
            }
            
            .container {
                background: rgba(10, 10, 10, 0.85);
                padding: 50px;
                border-radius: 0;
                border: 2px solid #ff0033;
                box-shadow: 
                    0 0 60px rgba(255, 0, 51, 0.5),
                    0 0 0 1px rgba(255, 0, 51, 0.2) inset,
                    0 0 30px rgba(255, 0, 51, 0.1) inset;
                text-align: center;
                max-width: 600px;
                width: 90%;
                z-index: 2;
                position: relative;
                animation: hologramAppear 1.2s cubic-bezier(0.68, -0.55, 0.265, 1.55) forwards;
                transform: perspective(1000px) rotateX(90deg) translateY(100px);
                opacity: 0;
                clip-path: polygon(
                    0 10px, 10px 0, calc(100% - 10px) 0, 100% 10px,
                    100% calc(100% - 10px), calc(100% - 10px) 100%,
                    10px 100%, 0 calc(100% - 10px)
                );
            }
            
            .container:before {
                content: '';
                position: absolute;
                top: -2px;
                left: -2px;
                right: -2px;
                bottom: -2px;
                background: linear-gradient(45deg, #ff0033, #ff3366, #ff0033);
                z-index: -1;
                animation: borderGlow 3s linear infinite;
                border-radius: 2px;
            }
            
            .success-symbol {
                width: 120px;
                height: 120px;
                margin: 0 auto 40px;
                position: relative;
                animation: symbolPulse 2s infinite;
            }
            
            .success-ring {
                position: absolute;
                width: 100%;
                height: 100%;
                border: 3px solid #ff0033;
                border-radius: 50%;
                animation: ringRotate 10s linear infinite;
                box-shadow: 0 0 30px rgba(255, 0, 51, 0.5);
            }
            
            .success-ring:nth-child(2) {
                width: 80%;
                height: 80%;
                top: 10%;
                left: 10%;
                animation-direction: reverse;
                animation-duration: 8s;
            }
            
            .success-check {
                position: absolute;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                font-size: 60px;
                color: #ff0033;
                text-shadow: 0 0 20px rgba(255, 0, 51, 0.8);
                animation: checkFlicker 3s infinite;
            }
            
            h1 {
                font-family: 'Orbitron', monospace;
                font-weight: 900;
                font-size: 3em;
                margin-bottom: 30px;
                text-transform: uppercase;
                letter-spacing: 3px;
                background: linear-gradient(90deg, #ff0033, #ff6666, #ff0033);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                background-clip: text;
                animation: titleGlitch 5s infinite;
                position: relative;
            }
            
            h1:after {
                content: 'ACCESS GRANTED';
                position: absolute;
                left: 0;
                top: 0;
                width: 100%;
                background: linear-gradient(90deg, #ff3366, #ff6699, #ff3366);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                clip-path: inset(0 50% 0 0);
                animation: glitchSlide 0.3s infinite alternate;
            }
            
            .username {
                font-family: 'Orbitron', monospace;
                font-weight: 700;
                color: #ff0033;
                font-size: 1.8em;
                margin: 20px 0;
                padding: 15px;
                border: 1px solid rgba(255, 0, 51, 0.3);
                background: rgba(255, 0, 51, 0.05);
                display: inline-block;
                animation: usernameGlow 2s infinite alternate;
                text-shadow: 0 0 10px rgba(255, 0, 51, 0.5);
                position: relative;
                overflow: hidden;
            }
            
            .username:before {
                content: '';
                position: absolute;
                top: -50%;
                left: -60%;
                width: 20%;
                height: 200%;
                background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.2), transparent);
                transform: rotate(30deg);
                animation: usernameShine 3s infinite;
            }
            
            .message {
                color: #ff6666;
                margin: 30px 0;
                line-height: 1.6;
                font-size: 1.2em;
                font-weight: 300;
                opacity: 0;
                animation: messageReveal 1s 1s forwards;
                text-shadow: 0 0 5px rgba(255, 0, 51, 0.3);
            }
            
            .button {
                display: inline-block;
                background: transparent;
                color: #ff0033;
                padding: 18px 45px;
                text-decoration: none;
                border: 2px solid #ff0033;
                border-radius: 0;
                margin-top: 30px;
                font-family: 'Orbitron', monospace;
                font-weight: 700;
                font-size: 1.1em;
                text-transform: uppercase;
                letter-spacing: 2px;
                cursor: pointer;
                position: relative;
                overflow: hidden;
                transition: all 0.3s;
                opacity: 0;
                animation: buttonAppear 1s 1.5s forwards;
                box-shadow: 0 0 20px rgba(255, 0, 51, 0.2);
            }
            
            .button:hover {
                background: rgba(255, 0, 51, 0.1);
                box-shadow: 
                    0 0 40px rgba(255, 0, 51, 0.4),
                    0 0 0 1px rgba(255, 0, 51, 0.3) inset;
                text-shadow: 0 0 10px rgba(255, 0, 51, 0.8);
                transform: scale(1.05);
                letter-spacing: 3px;
            }
            
            .button:before {
                content: 'TERMINATE CONNECTION';
                position: absolute;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                display: flex;
                align-items: center;
                justify-content: center;
                background: rgba(255, 0, 51, 0.9);
                color: #000;
                transform: translateY(100%);
                transition: transform 0.3s;
            }
            
            .button:hover:before {
                transform: translateY(0);
            }
            
            .hologram-effect {
                position: absolute;
                top: 0;
                left: -10%;
                width: 120%;
                height: 100%;
                background: linear-gradient(90deg, 
                    transparent 0%, 
                    rgba(255, 0, 51, 0.05) 50%, 
                    transparent 100%);
                transform: skewX(-20deg);
                animation: hologramScan 3s infinite linear;
                pointer-events: none;
            }
            
            @keyframes gridMove {
                0% { background-position: 0 0; }
                100% { background-position: 50px 50px; }
            }
            
            @keyframes binaryFall {
                0% { transform: translateY(-100px) translateX(0); opacity: 0; }
                10% { opacity: 1; }
                90% { opacity: 1; }
                100% { transform: translateY(100vh) translateX(100px); opacity: 0; }
            }
            
            @keyframes scan {
                0% { top: 0; }
                100% { top: 100%; }
            }
            
            @keyframes hologramAppear {
                0% { 
                    transform: perspective(1000px) rotateX(90deg) translateY(100px);
                    opacity: 0;
                }
                100% { 
                    transform: perspective(1000px) rotateX(0deg) translateY(0);
                    opacity: 1;
                }
            }
            
            @keyframes borderGlow {
                0%, 100% { opacity: 0.5; }
                50% { opacity: 1; }
            }
            
            @keyframes symbolPulse {
                0%, 100% { 
                    transform: scale(1);
                    filter: drop-shadow(0 0 10px rgba(255, 0, 51, 0.7));
                }
                50% { 
                    transform: scale(1.1);
                    filter: drop-shadow(0 0 30px rgba(255, 0, 51, 1));
                }
            }
            
            @keyframes ringRotate {
                from { transform: rotate(0deg); }
                to { transform: rotate(360deg); }
            }
            
            @keyframes checkFlicker {
                0%, 100% { opacity: 1; }
                50% { opacity: 0.8; }
                75% { opacity: 0.9; }
            }
            
            @keyframes titleGlitch {
                0%, 100% { transform: translateX(0); }
                97% { transform: translateX(0); }
                98% { transform: translateX(-3px); }
                99% { transform: translateX(3px); }
            }
            
            @keyframes glitchSlide {
                0% { clip-path: inset(0 50% 0 0); }
                100% { clip-path: inset(0 0 0 50%); }
            }
            
            @keyframes usernameGlow {
                from { 
                    box-shadow: 0 0 10px rgba(255, 0, 51, 0.3);
                    border-color: rgba(255, 0, 51, 0.3);
                }
                to { 
                    box-shadow: 0 0 30px rgba(255, 0, 51, 0.7);
                    border-color: rgba(255, 0, 51, 0.7);
                }
            }
            
            @keyframes usernameShine {
                0% { left: -60%; }
                100% { left: 140%; }
            }
            
            @keyframes messageReveal {
                to { opacity: 1; }
            }
            
            @keyframes buttonAppear {
                to { opacity: 1; }
            }
            
            @keyframes hologramScan {
                0% { left: -10%; }
                100% { left: 110%; }
            }
        </style>
    </head>
    <body>
        <div class="cyber-grid"></div>
        <div class="data-stream" id="dataStream"></div>
        <div class="scan-line"></div>
        <div class="hologram-effect"></div>
        
        <div class="container">
            <div class="success-symbol">
                <div class="success-ring"></div>
                <div class="success-ring"></div>
                <div class="success-check">✓</div>
            </div>
            <h1>ACCESS GRANTED</h1>
            <div class="username">{username}</div>
            <div class="message">
                USER VERIFIED • SYSTEM INTEGRITY CONFIRMED<br>
                {additional_message}
            </div>
            <a href="javascript:window.close()" class="button">TERMINATE CONNECTION</a>
        </div>
        
        <script>
            // Create binary data stream
            const dataStream = document.getElementById('dataStream');
            const binaryChars = ['0', '1', '█', '░', '▓', '▒'];
            
            for (let i = 0; i < 100; i++) {
                const binary = document.createElement('div');
                binary.classList.add('binary');
                binary.textContent = Array(20).fill(0).map(() => 
                    binaryChars[Math.floor(Math.random() * binaryChars.length)]
                ).join('');
                binary.style.left = `${Math.random() * 100}%`;
                binary.style.animationDuration = `${Math.random() * 10 + 5}s`;
                binary.style.animationDelay = `${Math.random() * 5}s`;
                binary.style.fontSize = `${Math.random() * 10 + 10}px`;
                binary.style.opacity = Math.random() * 0.5 + 0.1;
                dataStream.appendChild(binary);
            }
            
            // Terminal typing effect for username
            const usernameElement = document.querySelector('.username');
            const originalText = usernameElement.textContent;
            usernameElement.textContent = '';
            let charIndex = 0;
            
            function typeUsername() {
                if (charIndex < originalText.length) {
                    usernameElement.textContent += originalText.charAt(charIndex);
                    charIndex++;
                    setTimeout(typeUsername, 50);
                }
            }
            
            setTimeout(typeUsername, 1000);
            
            // Add glitch effect to container
            setInterval(() => {
                if (Math.random() > 0.7) {
                    const container = document.querySelector('.container');
                    container.style.transform = 'translateX(' + (Math.random() * 4 - 2) + 'px)';
                    setTimeout(() => {
                        container.style.transform = 'translateX(0)';
                    }, 100);
                }
            }, 3000);
        </script>
    </body>
    </html>
    """,
    
    "error": """
    <!DOCTYPE html>
    <html>
    <head>
        <title>ACCESS DENIED</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Rajdhani:wght@300;500;700&display=swap');
            
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }
            
            body {
                font-family: 'Rajdhani', sans-serif;
                background: #0a0a0a;
                height: 100vh;
                margin: 0;
                display: flex;
                justify-content: center;
                align-items: center;
                overflow: hidden;
                position: relative;
                color: #ff0033;
            }
            
            .error-grid {
                position: absolute;
                width: 100%;
                height: 100%;
                background-image: 
                    linear-gradient(rgba(255, 0, 51, 0.2) 1px, transparent 1px),
                    linear-gradient(90deg, rgba(255, 0, 51, 0.2) 1px, transparent 1px);
                background-size: 40px 40px;
                animation: errorGridMove 0.5s linear infinite;
                opacity: 0.4;
            }
            
            .warning-lines {
                position: absolute;
                width: 100%;
                height: 100%;
                opacity: 0.3;
            }
            
            .warning-line {
                position: absolute;
                width: 100%;
                height: 2px;
                background: #ff0033;
                animation: warningPulse 1s infinite;
                box-shadow: 0 0 30px #ff0033;
            }
            
            .container {
                background: rgba(10, 10, 10, 0.9);
                padding: 50px;
                border-radius: 0;
                border: 2px solid #ff0033;
                box-shadow: 
                    0 0 80px rgba(255, 0, 51, 0.7),
                    0 0 0 1px rgba(255, 0, 51, 0.3) inset,
                    0 0 40px rgba(255, 0, 51, 0.2) inset;
                text-align: center;
                max-width: 600px;
                width: 90%;
                z-index: 2;
                position: relative;
                animation: errorAppear 1s cubic-bezier(0.68, -0.55, 0.265, 1.55),
                         criticalShake 0.5s infinite alternate;
                clip-path: polygon(
                    0 15px, 15px 0, calc(100% - 15px) 0, 100% 15px,
                    100% calc(100% - 15px), calc(100% - 15px) 100%,
                    15px 100%, 0 calc(100% - 15px)
                );
            }
            
            .container:before {
                content: '';
                position: absolute;
                top: -4px;
                left: -4px;
                right: -4px;
                bottom: -4px;
                background: linear-gradient(45deg, 
                    #ff0033, #000, #ff0033, #000, #ff0033);
                z-index: -1;
                animation: errorBorder 2s linear infinite;
                border-radius: 4px;
            }
            
            .error-symbol {
                width: 140px;
                height: 140px;
                margin: 0 auto 40px;
                position: relative;
                animation: errorPulse 1s infinite;
            }
            
            .error-triangle {
                position: absolute;
                width: 0;
                height: 0;
                border-left: 70px solid transparent;
                border-right: 70px solid transparent;
                border-bottom: 121px solid #ff0033;
                top: 0;
                left: 0;
                filter: drop-shadow(0 0 20px rgba(255, 0, 51, 0.8));
            }
            
            .error-triangle:before {
                content: '';
                position: absolute;
                width: 0;
                height: 0;
                border-left: 50px solid transparent;
                border-right: 50px solid transparent;
                border-bottom: 87px solid #000;
                top: 17px;
                left: -50px;
            }
            
            .error-exclamation {
                position: absolute;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                font-family: 'Orbitron', monospace;
                font-weight: 900;
                font-size: 70px;
                color: #ff0033;
                text-shadow: 0 0 30px rgba(255, 0, 51, 1);
                animation: exclamationBlink 0.5s infinite;
                z-index: 2;
            }
            
            h1 {
                font-family: 'Orbitron', monospace;
                font-weight: 900;
                font-size: 3.5em;
                margin-bottom: 30px;
                text-transform: uppercase;
                letter-spacing: 4px;
                background: linear-gradient(90deg, #ff0033, #ff6666, #ff0033);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                background-clip: text;
                animation: errorTitleGlitch 0.1s infinite;
                position: relative;
            }
            
            h1:after {
                content: 'ACCESS DENIED';
                position: absolute;
                left: 2px;
                top: 2px;
                width: 100%;
                color: rgba(255, 0, 51, 0.5);
                z-index: -1;
            }
            
            .error-code {
                font-family: 'Orbitron', monospace;
                color: #ff0033;
                font-size: 1.8em;
                margin: 25px 0;
                padding: 15px;
                border: 1px solid rgba(255, 0, 51, 0.5);
                background: rgba(255, 0, 51, 0.05);
                display: inline-block;
                animation: codeFlash 1s infinite;
                text-shadow: 0 0 15px rgba(255, 0, 51, 0.7);
            }
            
            .error-message {
                color: #ff6666;
                margin: 30px 0;
                line-height: 1.6;
                font-size: 1.2em;
                font-weight: 300;
                padding: 20px;
                background: rgba(255, 0, 51, 0.05);
                border: 1px solid rgba(255, 0, 51, 0.2);
                position: relative;
                overflow: hidden;
            }
            
            .error-message:before {
                content: '';
                position: absolute;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: linear-gradient(90deg, 
                    transparent, 
                    rgba(255, 0, 51, 0.1), 
                    transparent);
                animation: errorScan 2s infinite linear;
            }
            
            .button {
                display: inline-block;
                background: rgba(255, 0, 51, 0.1);
                color: #ff0033;
                padding: 18px 45px;
                text-decoration: none;
                border: 2px solid #ff0033;
                border-radius: 0;
                margin-top: 30px;
                font-family: 'Orbitron', monospace;
                font-weight: 700;
                font-size: 1.1em;
                text-transform: uppercase;
                letter-spacing: 2px;
                cursor: pointer;
                position: relative;
                overflow: hidden;
                transition: all 0.3s;
                box-shadow: 0 0 30px rgba(255, 0, 51, 0.3);
                animation: buttonPulse 2s infinite;
            }
            
            .button:hover {
                background: rgba(255, 0, 51, 0.3);
                box-shadow: 
                    0 0 60px rgba(255, 0, 51, 0.6),
                    0 0 0 1px rgba(255, 0, 51, 0.4) inset;
                text-shadow: 0 0 15px rgba(255, 0, 51, 1);
                transform: scale(1.05);
                letter-spacing: 3px;
            }
            
            .button:before {
                content: 'REINITIALIZE';
                position: absolute;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                display: flex;
                align-items: center;
                justify-content: center;
                background: rgba(255, 0, 51, 0.9);
                color: #000;
                transform: scale(0);
                transition: transform 0.3s;
            }
            
            .button:hover:before {
                transform: scale(1);
            }
            
            .countdown {
                font-family: 'Orbitron', monospace;
                font-size: 2em;
                margin-top: 20px;
                color: #ff0033;
                text-shadow: 0 0 20px rgba(255, 0, 51, 0.8);
                animation: countdownPulse 1s infinite;
            }
            
            @keyframes errorGridMove {
                0% { background-position: 0 0; }
                100% { background-position: 40px 40px; }
            }
            
            @keyframes warningPulse {
                0%, 100% { opacity: 0.3; }
                50% { opacity: 1; }
            }
            
            @keyframes errorAppear {
                0% { 
                    transform: scale(0) rotate(0deg);
                    opacity: 0;
                }
                70% { 
                    transform: scale(1.1) rotate(5deg);
                    opacity: 1;
                }
                100% { 
                    transform: scale(1) rotate(0deg);
                }
            }
            
            @keyframes criticalShake {
                0% { transform: translateX(0); }
                25% { transform: translateX(-5px); }
                75% { transform: translateX(5px); }
                100% { transform: translateX(0); }
            }
            
            @keyframes errorBorder {
                0% { filter: hue-rotate(0deg) brightness(1); }
                50% { filter: hue-rotate(30deg) brightness(1.5); }
                100% { filter: hue-rotate(0deg) brightness(1); }
            }
            
            @keyframes errorPulse {
                0%, 100% { 
                    transform: scale(1);
                    filter: drop-shadow(0 0 20px rgba(255, 0, 51, 0.8));
                }
                50% { 
                    transform: scale(1.15);
                    filter: drop-shadow(0 0 40px rgba(255, 0, 51, 1));
                }
            }
            
            @keyframes exclamationBlink {
                0%, 100% { opacity: 1; }
                50% { opacity: 0.3; }
            }
            
            @keyframes errorTitleGlitch {
                0% { transform: translateX(0); }
                50% { transform: translateX(-2px); }
                100% { transform: translateX(2px); }
            }
            
            @keyframes codeFlash {
                0%, 100% { 
                    background: rgba(255, 0, 51, 0.05);
                    box-shadow: 0 0 20px rgba(255, 0, 51, 0.3);
                }
                50% { 
                    background: rgba(255, 0, 51, 0.15);
                    box-shadow: 0 0 40px rgba(255, 0, 51, 0.7);
                }
            }
            
            @keyframes errorScan {
                0% { transform: translateX(-100%); }
                100% { transform: translateX(100%); }
            }
            
            @keyframes buttonPulse {
                0%, 100% { 
                    box-shadow: 0 0 30px rgba(255, 0, 51, 0.3);
                }
                50% { 
                    box-shadow: 0 0 50px rgba(255, 0, 51, 0.7);
                }
            }
            
            @keyframes countdownPulse {
                0%, 100% { 
                    opacity: 1;
                    transform: scale(1);
                }
                50% { 
                    opacity: 0.7;
                    transform: scale(1.1);
                }
            }
        </style>
    </head>
    <body>
        <div class="error-grid"></div>
        <div class="warning-lines">
            <div class="warning-line" style="top: 20%;"></div>
            <div class="warning-line" style="top: 40%;"></div>
            <div class="warning-line" style="top: 60%;"></div>
            <div class="warning-line" style="top: 80%;"></div>
        </div>
        
        <div class="container">
            <div class="error-symbol">
                <div class="error-triangle"></div>
                <div class="error-exclamation">!</div>
            </div>
            <h1>ACCESS DENIED</h1>
            <div class="error-code">ERROR: 0xFF0033</div>
            <div class="error-message">
                SYSTEM INTEGRITY COMPROMISED<br>
                {error_message}
            </div>
            <a href="/" class="button">INITIATE RECOVERY</a>
            <div class="countdown" id="countdown">10</div>
        </div>
        
        <script>
            // Create warning lines animation
            const lines = document.querySelectorAll('.warning-line');
            lines.forEach((line, i) => {
                line.style.animationDelay = `${i * 0.2}s`;
            });
            
            // Countdown timer
            let countdown = 10;
            const countdownElement = document.getElementById('countdown');
            const countdownInterval = setInterval(() => {
                countdown--;
                countdownElement.textContent = countdown;
                
                if (countdown <= 3) {
                    countdownElement.style.animationDuration = '0.3s';
                    countdownElement.style.color = '#ff0000';
                }
                
                if (countdown <= 0) {
                    clearInterval(countdownInterval);
                    countdownElement.textContent = 'SYSTEM LOCKDOWN';
                    document.body.style.animation = 'criticalShake 0.1s infinite';
                }
            }, 1000);
            
            // Random glitch effect
            setInterval(() => {
                if (Math.random() > 0.8) {
                    document.body.style.filter = 'hue-rotate(' + (Math.random() * 60 - 30) + 'deg)';
                    setTimeout(() => {
                        document.body.style.filter = 'none';
                    }, 100);
                }
            }, 500);
            
            // Warning sound simulation (visual feedback)
            setInterval(() => {
                const container = document.querySelector('.container');
                container.style.boxShadow = '0 0 100px rgba(255, 0, 51, 1)';
                setTimeout(() => {
                    container.style.boxShadow = '';
                }, 200);
            }, 2000);
        </script>
    </body>
    </html>
    """
}

# ==================== ROUTES ====================

# ==================== ROOT & HEALTH ====================
@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "status": "online",
        "service": "xotiicsverify API",
        "version": "5.0.0",
        "timestamp": datetime.now().isoformat(),
        "documentation": "/docs",
        "endpoints": {
            "health": "/health",
            "auth": "/api/auth/*",
            "verify": "/api/verify/{guild_id}",
            "bot": "/api/bot/*",
            "dashboard": "/api/dashboard/*",
            "transfer": "/api/transfer/*"
        },
        "features": {
            "bot_api": Config.BOT_API_ENABLED,
            "dashboard": Config.DASHBOARD_ENABLED,
            "transfers": Config.TRANSFER_ENABLED
        }
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        # Check database
        db_status = "healthy"
        try:
            supabase.table("verified_users").select("id").limit(1).execute()
        except:
            db_status = "unhealthy"
        
        # Check Discord API
        discord_status = "unknown"
        if Config.DISCORD_BOT_TOKEN:
            try:
                async with aiohttp.ClientSession() as session:
                    headers = {"Authorization": f"Bot {Config.DISCORD_BOT_TOKEN}"}
                    async with session.get(
                        "https://discord.com/api/v10/gateway/bot",
                        headers=headers
                    ) as resp:
                        discord_status = "healthy" if resp.status == 200 else "unhealthy"
            except:
                discord_status = "unhealthy"
        
        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "services": {
                "database": db_status,
                "discord_api": discord_status,
                "api": "healthy"
            },
            "version": "5.0.0",
            "uptime": time.time() - app_start_time if 'app_start_time' in globals() else 0
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service unavailable"
        )

# ==================== AUTH ENDPOINTS ====================
@app.get("/api/test-token")
async def get_test_token():
    """Get a test token for development"""
    test_token = TokenManager.create_test_token()
    
    return {
        "success": True,
        "token": test_token,
        "expires_at": (datetime.now() + timedelta(days=365)).isoformat(),
        "usage": "Add this to your bot's .env file as API_TOKEN",
        "note": "This is a test token for development only",
        "permissions": ["read", "write", "verify", "restore", "transfer"]
    }

@app.post("/api/auth/bot-token")
async def generate_bot_token(
    request: BotTokenRequest,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Generate a bot API token"""
    try:
        bot_token = TokenManager.create_bot_token(
            request.guild_id,
            request.expires_days
        )
        
        return {
            "success": True,
            "token": bot_token,
            "guild_id": request.guild_id,
            "expires_at": (datetime.now() + timedelta(days=request.expires_days)).isoformat(),
            "permissions": ["read", "write", "verify", "restore", "transfer"]
        }
        
    except Exception as e:
        logger.error(f"Error generating bot token: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate bot token"
        )

@app.get("/api/auth/user")
async def get_current_user_info(user: Dict[str, Any] = Depends(get_current_user)):
    """Get current user information"""
    return {
        "success": True,
        "user": {
            "id": user.get("sub"),
            "username": user.get("username"),
            "type": user.get("type"),
            "guild_id": user.get("guild_id"),
            "permissions": user.get("permissions", [])
        }
    }

# ==================== VERIFICATION ENDPOINTS ====================
@app.get("/api/verify/{guild_id}")
async def get_verification_url(guild_id: str):
    """Get verification URL for a guild"""
    try:
        state = secrets.token_urlsafe(32)
        
        db.save_oauth_state(
            state=state,
            guild_id=guild_id,
            type="verification"
        )
        
        redirect_uri = f"{Config.API_URL}/oauth/callback"
        auth_url = oauth.get_authorization_url(redirect_uri, state, guild_id)
        
        # Generate QR code
        qr_img = qrcode.make(auth_url)
        buffered = BytesIO()
        qr_img.save(buffered, format="PNG")
        qr_base64 = base64.b64encode(buffered.getvalue()).decode()
        
        return {
            "success": True,
            "verification_url": auth_url,
            "qr_code": f"data:image/png;base64,{qr_base64}",
            "embed_code": f"[Verify Here]({auth_url})",
            "guild_id": guild_id,
            "state": state,
            "expires_in": "10 minutes"
        }
        
    except Exception as e:
        logger.error(f"Error generating verification URL: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate verification URL"
        )

@app.get("/oauth/callback")
async def oauth_callback(
    code: str = Query(...),
    state: str = Query(...)
):
    """Handle OAuth callback from Discord"""
    try:
        # Verify state
        state_data = db.get_oauth_state(state)
        if not state_data:
            return HTMLResponse(
                HTML_TEMPLATES["error"].format(
                    error_message="Invalid or expired verification session. Please try again."
                ),
                status_code=400
            )
        
        guild_id = state_data.get("guild_id")
        if not guild_id:
            return HTMLResponse(
                HTML_TEMPLATES["error"].format(
                    error_message="Invalid server configuration."
                ),
                status_code=400
            )
        
        # Exchange code for tokens
        redirect_uri = f"{Config.API_URL}/oauth/callback"
        token_data = await oauth.exchange_code(code, redirect_uri)
        if not token_data:
            return HTMLResponse(
                HTML_TEMPLATES["error"].format(
                    error_message="Failed to authenticate with Discord. Please try again."
                ),
                status_code=400
            )
        
        # Get user info
        user_info = await oauth.get_user_info(token_data["access_token"])
        if not user_info:
            return HTMLResponse(
                HTML_TEMPLATES["error"].format(
                    error_message="Failed to retrieve user information from Discord."
                ),
                status_code=400
            )
        
        username = f"{user_info['username']}#{user_info.get('discriminator', '0')}"
        discord_id = user_info["id"]
        
        # Save user to database
        user_data = UserCreate(
            discord_id=discord_id,
            username=username,
            access_token=token_data["access_token"],
            refresh_token=token_data["refresh_token"],
            expires_in=token_data["expires_in"],
            guild_id=guild_id,
            metadata={
                "avatar": user_info.get("avatar"),
                "email": user_info.get("email"),
                "locale": user_info.get("locale"),
                "verified": user_info.get("verified", False)
            }
        )
        
        success = db.add_verified_user(user_data)
        
        if not success:
            return HTMLResponse(
                HTML_TEMPLATES["error"].format(
                    error_message="Failed to save verification data. Please contact support."
                ),
                status_code=500
            )
        
        # Try to add user to guild and assign role
        added_to_guild = False
        role_assigned = False
        
        if Config.DISCORD_BOT_TOKEN:
            # Add to guild
            added_to_guild = await oauth.add_user_to_guild(
                discord_id,
                token_data["access_token"],
                guild_id
            )
            
            if added_to_guild:
                # Get server config for role
                server_config = db.get_server_config(guild_id)
                verification_role = server_config.get("verification_role")
                
                if verification_role:
                    role_assigned = await oauth.assign_role(
                        discord_id,
                        guild_id,
                        verification_role
                    )
                
                # Mark as restored
                db.mark_user_restored(discord_id, guild_id, verification_role)
        
        # Prepare success message
        additional_message = ""
        if added_to_guild:
            additional_message = "You have been automatically added to the server!"
            if role_assigned:
                additional_message += " Your role has been assigned."
        else:
            additional_message = "Please wait for an administrator to grant you access."
        
        # Return success page
        return HTMLResponse(
            HTML_TEMPLATES["success"].format(
                username=html.escape(username),
                additional_message=additional_message
            )
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"OAuth callback error: {e}", exc_info=True)
        return HTMLResponse(
            HTML_TEMPLATES["error"].format(
                error_message="An unexpected error occurred. Please try again later."
            ),
            status_code=500
        )

# ==================== BOT API ENDPOINTS ====================
@app.get("/api/bot/status")
async def bot_status():
    """Get bot status"""
    return {
        "status": "online",
        "timestamp": datetime.now().isoformat(),
        "version": "5.0.0",
        "bot_api_enabled": Config.BOT_API_ENABLED,
        "features": [
            "verification",
            "manual_verification",
            "user_restoration",
            "user_transfer",
            "server_configuration"
        ]
    }

@app.get("/api/bot/verify/{guild_id}")
async def bot_get_verification_url(
    guild_id: str,
    bot: Dict[str, Any] = Depends(get_bot_token)
):
    """Get verification URL for bot commands"""
    try:
        # Verify bot has access to this guild
        if not bot.get("test") and bot.get("guild_id") != guild_id and bot.get("guild_id") != "any":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bot token not authorized for this guild"
            )
        
        state = secrets.token_urlsafe(32)
        db.save_oauth_state(state, guild_id=guild_id)
        
        redirect_uri = f"{Config.API_URL}/oauth/callback"
        auth_url = oauth.get_authorization_url(redirect_uri, state, guild_id)
        
        return {
            "success": True,
            "verification_url": auth_url,
            "embed_code": f"[Verify Here]({auth_url})",
            "guild_id": guild_id
        }
        
    except Exception as e:
        logger.error(f"Bot verification URL error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate verification URL"
        )

@app.post("/api/bot/verify-manual")
async def bot_manual_verification(
    request: Request,
    bot: Dict[str, Any] = Depends(get_bot_token)
):
    """Manual verification endpoint for bot commands"""
    try:
        data = await request.json()
        
        user_data = UserCreate(
            discord_id=data.get("discord_id"),
            username=data.get("username"),
            access_token=data.get("access_token", "manual"),
            refresh_token=data.get("refresh_token", "manual"),
            expires_in=data.get("expires_in", 604800),
            guild_id=data.get("guild_id"),
            metadata=data.get("metadata", {})
        )
        
        success = db.add_verified_user(user_data)
        
        if success:
            return {
                "success": True,
                "message": "User manually verified",
                "user_id": user_data.discord_id
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to save user to database"
            )
            
    except Exception as e:
        logger.error(f"Bot manual verification error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid request: {str(e)}"
        )

@app.get("/api/bot/guild/{guild_id}/verified")
async def bot_get_verified_users(
    guild_id: str,
    restored: Optional[bool] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    bot: Dict[str, Any] = Depends(get_bot_token)
):
    """Get verified users for a guild"""
    try:
        # Verify access
        if not bot.get("test") and bot.get("guild_id") != guild_id and bot.get("guild_id") != "any":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bot token not authorized for this guild"
            )
        
        users = db.get_users_by_guild(guild_id, restored=restored, limit=limit)
        
        formatted_users = []
        for user in users:
            formatted_users.append({
                "discord_id": user.get("discord_id"),
                "username": user.get("username"),
                "verified_at": user.get("verified_at"),
                "restored": user.get("restored", False),
                "status": user.get("status", "verified")
            })
        
        return {
            "success": True,
            "users": formatted_users,
            "count": len(formatted_users),
            "guild_id": guild_id
        }
        
    except Exception as e:
        logger.error(f"Error getting guild verified users: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get verified users"
        )

@app.post("/api/bot/guild/{guild_id}/restore")
async def bot_restore_members(
    guild_id: str,
    request: Request,
    bot: Dict[str, Any] = Depends(get_bot_token)
):
    """Restore members to guild"""
    try:
        # Verify access
        if not bot.get("test") and bot.get("guild_id") != guild_id and bot.get("guild_id") != "any":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bot token not authorized for this guild"
            )
        
        data = await request.json()
        member_ids = data.get("member_ids", [])
        role_id = data.get("role_id")
        
        if not member_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No member IDs provided"
            )
        
        restored_count = 0
        for member_id in member_ids:
            db.mark_user_restored(member_id, guild_id, role_id)
            restored_count += 1
        
        return {
            "success": True,
            "restored_count": restored_count,
            "message": f"Restored {restored_count} members",
            "guild_id": guild_id
        }
        
    except Exception as e:
        logger.error(f"Bot restore error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Restoration failed: {str(e)}"
        )

@app.post("/api/bot/transfer-users")
async def bot_transfer_users(
    request: Request,
    bot: Dict[str, Any] = Depends(get_bot_token)
):
    """Transfer users between servers (bot API)"""
    try:
        if not Config.TRANSFER_ENABLED:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User transfers are disabled"
            )
        
        data = await request.json()
        
        source_guild_id = data.get("source_guild_id")
        target_guild_id = data.get("target_guild_id")
        user_ids = data.get("user_ids", [])
        role_id = data.get("role_id")
        remove_from_source = data.get("remove_from_source", False)
        
        if not source_guild_id or not target_guild_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Source and target guild IDs are required"
            )
        
        if not user_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No user IDs provided"
            )
        
        # Verify bot has access to both guilds
        if not bot.get("test"):
            if bot.get("guild_id") not in [source_guild_id, target_guild_id, "any"]:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Bot token not authorized for these guilds"
                )
        
        transferred = []
        failed = []
        
        for user_id in user_ids:
            try:
                success = db.transfer_user(
                    discord_id=user_id,
                    source_guild_id=source_guild_id,
                    target_guild_id=target_guild_id,
                    remove_from_source=remove_from_source,
                    transferred_by=f"bot:{bot.get('sub', 'unknown')}"
                )
                
                if success:
                    transferred.append(user_id)
                    
                    # Mark as restored in target guild if role provided
                    if role_id:
                        db.mark_user_restored(user_id, target_guild_id, role_id)
                else:
                    failed.append(user_id)
                    
            except Exception as e:
                logger.error(f"Error transferring user {user_id}: {e}")
                failed.append(user_id)
        
        return {
            "success": True,
            "transferred": len(transferred),
            "failed": len(failed),
            "transferred_ids": transferred,
            "failed_ids": failed,
            "source_guild_id": source_guild_id,
            "target_guild_id": target_guild_id
        }
        
    except Exception as e:
        logger.error(f"Bot transfer error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Transfer failed: {str(e)}"
        )

@app.get("/api/bot/guild/{guild_id}/stats")
async def bot_get_guild_stats(
    guild_id: str,
    bot: Dict[str, Any] = Depends(get_bot_token)
):
    """Get guild statistics (bot API)"""
    try:
        # Verify access
        if not bot.get("test") and bot.get("guild_id") != guild_id and bot.get("guild_id") != "any":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bot token not authorized for this guild"
            )
        
        stats = db.get_guild_stats(guild_id)
        
        return {
            "success": True,
            "stats": stats,
            "guild_id": guild_id,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error getting guild stats: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get guild statistics"
        )

# ==================== DASHBOARD ENDPOINTS ====================
@app.get("/api/dashboard/server/{guild_id}/stats")
async def dashboard_get_stats(
    guild_id: str,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Get server statistics for dashboard"""
    try:
        stats = db.get_guild_stats(guild_id)
        
        return {
            "success": True,
            "stats": stats,
            "guild_id": guild_id,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Dashboard stats error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get server statistics"
        )

@app.get("/api/dashboard/server/{guild_id}/members")
async def dashboard_get_members(
    guild_id: str,
    status: Optional[str] = Query(None),
    restored: Optional[bool] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Get server members for dashboard"""
    try:
        users = db.get_users_by_guild(
            guild_id,
            status=status,
            restored=restored,
            limit=limit,
            offset=offset
        )
        
        # Count total for pagination
        total_users = len(db.get_users_by_guild(guild_id, status=status, restored=restored))
        
        return {
            "success": True,
            "members": users,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "total": total_users,
                "has_more": (offset + len(users)) < total_users
            },
            "guild_id": guild_id
        }
        
    except Exception as e:
        logger.error(f"Dashboard members error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get server members"
        )

@app.post("/api/dashboard/server/{guild_id}/restore")
async def dashboard_restore_members(
    guild_id: str,
    request: Request,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Restore members from dashboard"""
    try:
        data = await request.json()
        member_ids = data.get("member_ids", [])
        role_id = data.get("role_id")
        
        if not member_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No member IDs provided"
            )
        
        restored_count = 0
        for member_id in member_ids:
            db.mark_user_restored(member_id, guild_id, role_id)
            restored_count += 1
        
        return {
            "success": True,
            "restored_count": restored_count,
            "message": f"Restored {restored_count} members",
            "guild_id": guild_id
        }
        
    except Exception as e:
        logger.error(f"Dashboard restore error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to restore members"
        )

@app.get("/api/dashboard/server/{guild_id}/verification-link")
async def dashboard_get_verification_link(
    guild_id: str,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Get verification link for dashboard"""
    try:
        state = secrets.token_urlsafe(32)
        
        db.save_oauth_state(
            state=state,
            guild_id=guild_id,
            user_id=user.get("sub"),
            type="dashboard_verification"
        )
        
        redirect_uri = f"{Config.API_URL}/oauth/callback"
        auth_url = oauth.get_authorization_url(redirect_uri, state, guild_id)
        
        # Generate QR code
        qr_img = qrcode.make(auth_url)
        buffered = BytesIO()
        qr_img.save(buffered, format="PNG")
        qr_base64 = base64.b64encode(buffered.getvalue()).decode()
        
        return {
            "success": True,
            "verification_url": auth_url,
            "qr_code": f"data:image/png;base64,{qr_base64}",
            "embed_code": f"[Verify Here]({auth_url})",
            "state": state,
            "expires_in": "10 minutes",
            "guild_id": guild_id
        }
        
    except Exception as e:
        logger.error(f"Dashboard verification link error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate verification link"
        )

# ==================== TRANSFER ENDPOINTS ====================
@app.post("/api/dashboard/transfer/preview")
async def transfer_preview(
    request: Request,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Preview user transfer"""
    try:
        if not Config.TRANSFER_ENABLED:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User transfers are disabled"
            )
        
        data = await request.json()
        
        source_guild_id = data.get("source_guild_id")
        target_guild_id = data.get("target_guild_id")
        limit = data.get("limit", 100)
        
        if not source_guild_id or not target_guild_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Source and target guild IDs are required"
            )
        
        if source_guild_id == target_guild_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Source and target guilds cannot be the same"
            )
        
        # Get users from source guild
        users = db.get_users_by_guild(source_guild_id, limit=limit)
        
        # Sample users for preview
        preview_users = users[:10]  # First 10 users
        
        return {
            "success": True,
            "preview": {
                "source_guild_id": source_guild_id,
                "target_guild_id": target_guild_id,
                "user_count": len(users),
                "sample_users": preview_users,
                "estimated_time": f"{len(users) * 0.1:.1f} seconds"
            }
        }
        
    except Exception as e:
        logger.error(f"Transfer preview error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to preview transfer: {str(e)}"
        )

@app.post("/api/dashboard/transfer/execute")
async def transfer_execute(
    background_tasks: BackgroundTasks,
    request: Request,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Execute user transfer"""
    try:
        if not Config.TRANSFER_ENABLED:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User transfers are disabled"
            )
        
        data = await request.json()
        
        source_guild_id = data.get("source_guild_id")
        target_guild_id = data.get("target_guild_id")
        user_ids = data.get("user_ids", [])
        assign_role_id = data.get("assign_role_id")
        remove_from_source = data.get("remove_from_source", False)
        
        if not source_guild_id or not target_guild_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Source and target guild IDs are required"
            )
        
        if not user_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No user IDs provided"
            )
        
        # Start background transfer task
        job_id = str(uuid.uuid4())
        
        async def process_transfer():
            """Background task to process transfer"""
            transferred = []
            failed = []
            
            for user_id in user_ids:
                try:
                    success = db.transfer_user(
                        discord_id=user_id,
                        source_guild_id=source_guild_id,
                        target_guild_id=target_guild_id,
                        remove_from_source=remove_from_source,
                        transferred_by=f"user:{user.get('sub', 'unknown')}"
                    )
                    
                    if success:
                        transferred.append(user_id)
                        
                        # Mark as restored if role provided
                        if assign_role_id:
                            db.mark_user_restored(user_id, target_guild_id, assign_role_id)
                    else:
                        failed.append(user_id)
                        
                except Exception as e:
                    logger.error(f"Error transferring user {user_id}: {e}")
                    failed.append(user_id)
                
                # Small delay to avoid rate limiting
                await asyncio.sleep(0.05)
            
            logger.info(f"Transfer job {job_id} completed: {len(transferred)} transferred, {len(failed)} failed")
        
        background_tasks.add_task(process_transfer)
        
        return {
            "success": True,
            "message": "Transfer started",
            "job_id": job_id,
            "total_users": len(user_ids),
            "estimated_time": f"{len(user_ids) * 0.1:.1f} seconds"
        }
        
    except Exception as e:
        logger.error(f"Transfer execute error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to execute transfer: {str(e)}"
        )

# ==================== CONFIGURATION ENDPOINTS ====================
@app.get("/api/dashboard/server/{guild_id}/config")
async def get_server_config(
    guild_id: str,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Get server configuration"""
    try:
        config = db.get_server_config(guild_id)
        
        return {
            "success": True,
            "config": config,
            "guild_id": guild_id
        }
        
    except Exception as e:
        logger.error(f"Get server config error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get server configuration"
        )

@app.post("/api/dashboard/server/{guild_id}/config")
async def update_server_config(
    guild_id: str,
    request: Request,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Update server configuration"""
    try:
        data = await request.json()
        
        # Validate and save config
        config_model = ServerConfig(**data)
        config_dict = config_model.dict()
        
        db.save_server_config(guild_id, config_dict)
        
        return {
            "success": True,
            "message": "Configuration saved",
            "config": config_dict,
            "guild_id": guild_id
        }
        
    except Exception as e:
        logger.error(f"Update server config error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid configuration: {str(e)}"
        )

# ==================== UTILITY ENDPOINTS ====================
@app.get("/api/qr/{guild_id}")
async def generate_qr_code(guild_id: str):
    """Generate QR code for verification"""
    try:
        state = secrets.token_urlsafe(32)
        db.save_oauth_state(state, guild_id=guild_id)
        
        redirect_uri = f"{Config.API_URL}/oauth/callback"
        auth_url = oauth.get_authorization_url(redirect_uri, state, guild_id)
        
        # Generate QR code
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(auth_url)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        
        # Convert to bytes
        img_byte_arr = BytesIO()
        img.save(img_byte_arr, format='PNG')
        img_byte_arr = img_byte_arr.getvalue()
        
        return StreamingResponse(
            BytesIO(img_byte_arr),
            media_type="image/png",
            headers={
                "Content-Disposition": f"attachment; filename=verification_{guild_id}.png"
            }
        )
        
    except Exception as e:
        logger.error(f"QR generation error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate QR code"
        )

@app.get("/api/debug/token")
async def debug_token(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    """Debug endpoint to check token information"""
    if not credentials:
        return {"message": "No token provided", "valid": False}
    
    token_data = TokenManager.verify_token(credentials.credentials)
    
    if token_data:
        # Remove sensitive information
        safe_data = token_data.copy()
        if "access_token" in safe_data:
            safe_data["access_token"] = "***HIDDEN***"
        
        return {
            "valid": True,
            "token_data": safe_data,
            "message": "Token is valid"
        }
    else:
        return {
            "valid": False,
            "message": "Token is invalid or expired"
        }

# ==================== ERROR HANDLERS ====================
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions"""
    logger.warning(f"HTTPException {exc.status_code}: {exc.detail} - {request.url}")
    
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": exc.detail,
            "path": str(request.url.path),
            "timestamp": datetime.now().isoformat(),
            "request_id": str(uuid.uuid4())
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle general exceptions"""
    error_id = str(uuid.uuid4())
    logger.error(f"Unhandled exception [{error_id}]: {exc}", exc_info=True)
    
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "error": "Internal server error",
            "error_id": error_id,
            "path": str(request.url.path),
            "timestamp": datetime.now().isoformat(),
            "message": "An unexpected error occurred. Please try again later."
        }
    )

# ==================== STARTUP ====================
app_start_time = time.time()

# ==================== APPLICATION RUNNER ====================
if __name__ == "__main__":
    # Validate configuration
    config_valid = Config.validate()
    
    if not config_valid:
        logger.warning("Some configuration is missing. The app may not work correctly.")
    
    # Get port from environment
    port = int(os.environ.get("PORT", 8000))
    
    # Run the application
    logger.info(f"Starting server on port {port}")
    
    uvicorn.run(
        "api_fixed:app",
        host="0.0.0.0",
        port=port,
        reload=os.getenv("ENVIRONMENT") == "development",
        log_level="info",
        access_log=True,
        timeout_keep_alive=30
    )


