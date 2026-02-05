from fastapi import FastAPI, HTTPException, Request, Depends, status
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
import uvicorn
import os
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
import json
import aiohttp
import urllib.parse
from supabase import create_client, Client
from cryptography.fernet import Fernet
import base64
import jwt
from pydantic import BaseModel, Field
import asyncpg
import asyncio
from contextlib import asynccontextmanager
import time
import uuid

# ==================== CONFIGURATION ====================
class Config:
    # Environment variables with defaults for development
    DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
    DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
    DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
    SUPABASE_URL = os.getenv("SUPABASE_URL", "")
    SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    REDIRECT_URI = os.getenv("REDIRECT_URI", "https://bot-hosting-b.onrender.com/oauth/callback")
    API_URL = os.getenv("API_URL", "https://bot-hosting-b.onrender.com")
    FRONTEND_URL = os.getenv("FRONTEND_URL", "https://bothostingf.vercel.app")
    JWT_SECRET = os.getenv("JWT_SECRET", secrets.token_urlsafe(32))
    ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", base64.urlsafe_b64encode(Fernet.generate_key()).decode())
    CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")

    @classmethod
    def validate(cls):
        required = ["DISCORD_CLIENT_ID", "DISCORD_CLIENT_SECRET", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"]
        missing = [var for var in required if not getattr(cls, var)]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

Config.validate()

# ==================== LOGGING ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('app.log')
    ]
)
logger = logging.getLogger(__name__)

# ==================== LIFESPAN ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting xotiicsverify API...")
    logger.info(f"Environment: {os.getenv('ENVIRONMENT', 'production')}")
    logger.info(f"CORS Origins: {Config.CORS_ORIGINS}")
    
    # Create database tables if they don't exist
    await DatabaseManager.create_tables()
    logger.info("Database tables initialized")
    
    yield
    
    # Shutdown
    logger.info("Shutting down xotiicsverify API...")

# ==================== FASTAPI APP ====================
app = FastAPI(
    title="xotiicsverify API",
    description="Secure Discord verification system backend with dashboard",
    version="4.0.0",
    docs_url="/docs" if os.getenv("ENVIRONMENT") == "development" else None,
    redoc_url=None,
    lifespan=lifespan
)

# ==================== CORS ====================
app.add_middleware(
    CORSMiddleware,
    allow_origins=Config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# ==================== SECURITY ====================
security = HTTPBearer(auto_error=False)

# ==================== SUPABASE ====================
try:
    supabase: Client = create_client(Config.SUPABASE_URL, Config.SUPABASE_SERVICE_ROLE_KEY)
    logger.info("Supabase client initialized")
except Exception as e:
    logger.error(f"Failed to initialize Supabase: {e}")
    raise

# ==================== MODELS ====================
class BotConfig(BaseModel):
    default_role: Optional[str] = None
    auto_assign_role: bool = True
    send_welcome_dm: bool = False
    min_account_age: int = Field(7, ge=0)
    verification_timeout: int = Field(15, ge=1, le=60)
    require_email: bool = True
    enable_captcha: bool = False
    welcome_message: Optional[str] = None

class ServerConfig(BaseModel):
    verification_channel: Optional[str] = None
    verification_role: Optional[str] = None
    welcome_message: Optional[str] = None
    enable_auto_verification: bool = True
    log_channel: Optional[str] = None
    admin_roles: List[str] = []

class UserCreate(BaseModel):
    discord_id: str
    username: str
    access_token: str
    refresh_token: str
    expires_in: int
    guild_id: str
    metadata: Dict[str, Any] = {}

# ==================== DATABASE MANAGER ====================
class DatabaseManager:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        self.cipher_key = Config.ENCRYPTION_KEY.encode()
        key = base64.urlsafe_b64encode(self.cipher_key[:32].ljust(32, b'0'))
        self.cipher = Fernet(key)
    
    @staticmethod
    async def create_tables():
        """Create required tables if they don't exist"""
        try:
            # OAuth states table
            supabase.table("oauth_states").create(
                {
                    "name": "oauth_states",
                    "columns": [
                        {"name": "id", "type": "uuid", "default": "gen_random_uuid()", "primary_key": True},
                        {"name": "state", "type": "text", "unique": True, "not_null": True},
                        {"name": "user_id", "type": "text"},
                        {"name": "guild_id", "type": "text"},
                        {"name": "redirect_url", "type": "text"},
                        {"name": "created_at", "type": "timestamptz", "default": "now()"},
                        {"name": "expires_at", "type": "timestamptz", "default": "now() + interval '10 minutes'"}
                    ]
                },
                if_not_exists=True
            ).execute()
            
            # Verified users table
            supabase.table("verified_users").create(
                {
                    "name": "verified_users",
                    "columns": [
                        {"name": "id", "type": "uuid", "default": "gen_random_uuid()", "primary_key": True},
                        {"name": "discord_id", "type": "text", "not_null": True},
                        {"name": "username", "type": "text", "not_null": True},
                        {"name": "access_token", "type": "text", "not_null": True},
                        {"name": "refresh_token", "type": "text", "not_null": True},
                        {"name": "expires_at", "type": "timestamptz", "not_null": True},
                        {"name": "guild_id", "type": "text", "not_null": True},
                        {"name": "metadata", "type": "jsonb", "default": "'{}'::jsonb"},
                        {"name": "verified_at", "type": "timestamptz", "default": "now()"},
                        {"name": "restored", "type": "boolean", "default": "false"},
                        {"name": "restored_at", "type": "timestamptz"},
                        {"name": "restored_role_id", "type": "text"},
                        {"name": "status", "type": "text", "default": "'verified'"},
                        {"name": "created_at", "type": "timestamptz", "default": "now()"},
                        {"name": "updated_at", "type": "timestamptz", "default": "now()"}
                    ],
                    "indexes": [
                        {"name": "idx_verified_users_discord_guild", "columns": ["discord_id", "guild_id"], "unique": True},
                        {"name": "idx_verified_users_guild_status", "columns": ["guild_id", "status"]},
                        {"name": "idx_verified_users_verified_at", "columns": ["verified_at"]}
                    ]
                },
                if_not_exists=True
            ).execute()
            
            # Bot configs table
            supabase.table("bot_configs").create(
                {
                    "name": "bot_configs",
                    "columns": [
                        {"name": "user_id", "type": "text", "primary_key": True},
                        {"name": "config", "type": "jsonb", "default": "'{}'::jsonb"},
                        {"name": "updated_at", "type": "timestamptz", "default": "now()"}
                    ]
                },
                if_not_exists=True
            ).execute()
            
            # Server configs table
            supabase.table("server_configs").create(
                {
                    "name": "server_configs",
                    "columns": [
                        {"name": "guild_id", "type": "text", "primary_key": True},
                        {"name": "config", "type": "jsonb", "default": "'{}'::jsonb"},
                        {"name": "updated_at", "type": "timestamptz", "default": "now()"}
                    ]
                },
                if_not_exists=True
            ).execute()
            
            # Logs table
            supabase.table("logs").create(
                {
                    "name": "logs",
                    "columns": [
                        {"name": "id", "type": "uuid", "default": "gen_random_uuid()", "primary_key": True},
                        {"name": "guild_id", "type": "text", "not_null": True},
                        {"name": "type", "type": "text", "not_null": True},
                        {"name": "message", "type": "text", "not_null": True},
                        {"name": "user_id", "type": "text"},
                        {"name": "metadata", "type": "jsonb", "default": "'{}'::jsonb"},
                        {"name": "created_at", "type": "timestamptz", "default": "now()"}
                    ],
                    "indexes": [
                        {"name": "idx_logs_guild_created", "columns": ["guild_id", "created_at"]},
                        {"name": "idx_logs_type", "columns": ["type"]}
                    ]
                },
                if_not_exists=True
            ).execute()
            
            # API keys table
            supabase.table("api_keys").create(
                {
                    "name": "api_keys",
                    "columns": [
                        {"name": "id", "type": "uuid", "default": "gen_random_uuid()", "primary_key": True},
                        {"name": "key", "type": "text", "unique": True, "not_null": True},
                        {"name": "user_id", "type": "text", "not_null": True},
                        {"name": "name", "type": "text"},
                        {"name": "permissions", "type": "jsonb", "default": "'{}'::jsonb"},
                        {"name": "last_used", "type": "timestamptz"},
                        {"name": "expires_at", "type": "timestamptz"},
                        {"name": "created_at", "type": "timestamptz", "default": "now()"}
                    ]
                },
                if_not_exists=True
            ).execute()
            
            logger.info("All tables created or verified")
            
        except Exception as e:
            logger.error(f"Error creating tables: {e}")
            raise
    
    def _encrypt(self, data: str) -> str:
        """Encrypt sensitive data"""
        try:
            return self.cipher.encrypt(data.encode()).decode()
        except Exception as e:
            logger.error(f"Encryption error: {e}")
            raise
    
    def _decrypt(self, encrypted_data: str) -> str:
        """Decrypt sensitive data"""
        try:
            return self.cipher.decrypt(encrypted_data.encode()).decode()
        except Exception as e:
            logger.error(f"Decryption error: {e}")
            raise
    
    # OAuth State Management
    def save_oauth_state(self, state: str, **kwargs) -> bool:
        """Save OAuth state with optional metadata"""
        try:
            data = {
                "state": state,
                "user_id": kwargs.get("user_id"),
                "guild_id": kwargs.get("guild_id"),
                "redirect_url": kwargs.get("redirect_url"),
                "type": kwargs.get("type", "auth"),
                "metadata": kwargs.get("metadata", {})
            }
            
            # Clean expired states first
            supabase.table("oauth_states")\
                .delete()\
                .lt("expires_at", datetime.now().isoformat())\
                .execute()
            
            supabase.table("oauth_states").insert(data).execute()
            logger.info(f"Saved OAuth state: {state[:8]}...")
            return True
            
        except Exception as e:
            logger.error(f"Error saving OAuth state: {e}")
            return False
    
    def get_oauth_state(self, state: str) -> Optional[Dict[str, Any]]:
        """Get and delete OAuth state"""
        try:
            response = supabase.table("oauth_states")\
                .select("*")\
                .eq("state", state)\
                .gt("expires_at", datetime.now().isoformat())\
                .execute()
            
            if response.data:
                state_data = response.data[0]
                
                # Delete the state after retrieval
                supabase.table("oauth_states").delete().eq("state", state).execute()
                
                return {
                    "state": state_data["state"],
                    "user_id": state_data.get("user_id"),
                    "guild_id": state_data.get("guild_id"),
                    "redirect_url": state_data.get("redirect_url"),
                    "type": state_data.get("type", "auth"),
                    "metadata": state_data.get("metadata", {})
                }
                
        except Exception as e:
            logger.error(f"Error getting OAuth state: {e}")
        
        return None
    
    # User Management
    def add_verified_user(self, user_data: UserCreate) -> bool:
        """Add or update a verified user"""
        try:
            expires_at = datetime.now() + timedelta(seconds=user_data.expires_in)
            
            data = {
                "discord_id": user_data.discord_id,
                "username": user_data.username,
                "access_token": self._encrypt(user_data.access_token),
                "refresh_token": self._encrypt(user_data.refresh_token),
                "expires_at": expires_at.isoformat(),
                "guild_id": user_data.guild_id,
                "metadata": user_data.metadata,
                "status": "verified"
            }
            
            supabase.table("verified_users").upsert(
                data,
                on_conflict="discord_id,guild_id"
            ).execute()
            
            logger.info(f"User added/updated: {user_data.username} ({user_data.discord_id})")
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
                return {
                    "discord_id": user["discord_id"],
                    "username": user["username"],
                    "access_token": self._decrypt(user["access_token"]),
                    "refresh_token": self._decrypt(user["refresh_token"]),
                    "expires_at": datetime.fromisoformat(user["expires_at"].replace("Z", "+00:00")),
                    "guild_id": user["guild_id"],
                    "metadata": user.get("metadata", {}),
                    "verified_at": datetime.fromisoformat(user["verified_at"].replace("Z", "+00:00")) if user.get("verified_at") else None,
                    "restored": user.get("restored", False),
                    "restored_at": datetime.fromisoformat(user["restored_at"].replace("Z", "+00:00")) if user.get("restored_at") else None,
                    "restored_role_id": user.get("restored_role_id"),
                    "status": user.get("status", "pending")
                }
                
        except Exception as e:
            logger.error(f"Error getting user: {e}")
        
        return None
    
    def get_guild_users(self, guild_id: str, **kwargs) -> List[Dict[str, Any]]:
        """Get users for a guild with filtering"""
        try:
            query = supabase.table("verified_users")\
                .select("*")\
                .eq("guild_id", guild_id)\
                .order("verified_at", desc=True)
            
            # Apply filters
            if kwargs.get("status"):
                query = query.eq("status", kwargs["status"])
            
            if kwargs.get("restored") is not None:
                query = query.eq("restored", kwargs["restored"])
            
            if kwargs.get("limit"):
                query = query.limit(kwargs["limit"])
            
            if kwargs.get("offset"):
                query = query.range(kwargs["offset"], kwargs["offset"] + (kwargs.get("limit") or 100) - 1)
            
            response = query.execute()
            
            users = []
            for user in response.data:
                users.append({
                    "discord_id": user["discord_id"],
                    "username": user["username"],
                    "guild_id": user["guild_id"],
                    "verified_at": user.get("verified_at"),
                    "restored": user.get("restored", False),
                    "restored_at": user.get("restored_at"),
                    "restored_role_id": user.get("restored_role_id"),
                    "status": user.get("status", "pending"),
                    "metadata": user.get("metadata", {})
                })
            
            return users
            
        except Exception as e:
            logger.error(f"Error getting guild users: {e}")
            return []
    
    def get_guild_stats(self, guild_id: str) -> Dict[str, Any]:
        """Get statistics for a guild"""
        try:
            # Total verified
            total_resp = supabase.table("verified_users")\
                .select("id", count="exact")\
                .eq("guild_id", guild_id)\
                .execute()
            total = total_resp.count or 0
            
            # Restored
            restored_resp = supabase.table("verified_users")\
                .select("id", count="exact")\
                .eq("guild_id", guild_id)\
                .eq("restored", True)\
                .execute()
            restored = restored_resp.count or 0
            
            # Verified today
            today = datetime.now().date().isoformat()
            today_resp = supabase.table("verified_users")\
                .select("id", count="exact")\
                .eq("guild_id", guild_id)\
                .gte("verified_at", f"{today}T00:00:00")\
                .execute()
            today_count = today_resp.count or 0
            
            # Verified this week
            week_ago = (datetime.now() - timedelta(days=7)).isoformat()
            week_resp = supabase.table("verified_users")\
                .select("id", count="exact")\
                .eq("guild_id", guild_id)\
                .gte("verified_at", week_ago)\
                .execute()
            week_count = week_resp.count or 0
            
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
        """Mark a user as restored"""
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
            
            logger.info(f"User marked as restored: {discord_id}")
            
        except Exception as e:
            logger.error(f"Error marking user restored: {e}")
    
    # Configuration Management
    def save_bot_config(self, user_id: str, config: Dict[str, Any]):
        """Save bot configuration"""
        try:
            data = {
                "user_id": user_id,
                "config": config,
                "updated_at": datetime.now().isoformat()
            }
            
            supabase.table("bot_configs").upsert(data, on_conflict="user_id").execute()
            logger.info(f"Bot config saved for user: {user_id}")
            
        except Exception as e:
            logger.error(f"Error saving bot config: {e}")
    
    def get_bot_config(self, user_id: str) -> Dict[str, Any]:
        """Get bot configuration"""
        try:
            response = supabase.table("bot_configs")\
                .select("config")\
                .eq("user_id", user_id)\
                .execute()
            
            if response.data:
                return response.data[0]["config"]
                
        except Exception as e:
            logger.error(f"Error getting bot config: {e}")
        
        return {}
    
    def save_server_config(self, guild_id: str, config: Dict[str, Any]):
        """Save server configuration"""
        try:
            data = {
                "guild_id": guild_id,
                "config": config,
                "updated_at": datetime.now().isoformat()
            }
            
            supabase.table("server_configs").upsert(data, on_conflict="guild_id").execute()
            logger.info(f"Server config saved for guild: {guild_id}")
            
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
    
    # Log Management
    def add_log(self, log_type: str, message: str, **kwargs):
        """Add a log entry"""
        try:
            data = {
                "guild_id": kwargs.get("guild_id", "system"),
                "type": log_type,
                "message": message,
                "user_id": kwargs.get("user_id"),
                "metadata": kwargs.get("metadata", {}),
                "created_at": datetime.now().isoformat()
            }
            
            supabase.table("logs").insert(data).execute()
            
        except Exception as e:
            logger.error(f"Error adding log: {e}")
    
    def get_logs(self, guild_id: str, **kwargs) -> List[Dict[str, Any]]:
        """Get logs for a guild"""
        try:
            query = supabase.table("logs")\
                .select("*")\
                .eq("guild_id", guild_id)\
                .order("created_at", desc=True)
            
            if kwargs.get("log_type") and kwargs["log_type"] != "all":
                query = query.eq("type", kwargs["log_type"])
            
            if kwargs.get("limit"):
                query = query.limit(kwargs["limit"])
            
            response = query.execute()
            return response.data
            
        except Exception as e:
            logger.error(f"Error getting logs: {e}")
            return []

# ==================== OAUTH HANDLER ====================
class OAuthHandler:
    def __init__(self):
        self.client_id = Config.DISCORD_CLIENT_ID
        self.client_secret = Config.DISCORD_CLIENT_SECRET
        self.redirect_uri = Config.REDIRECT_URI
        self.bot_token = Config.DISCORD_BOT_TOKEN
    
    async def exchange_code(self, code: str) -> Optional[Dict[str, Any]]:
        """Exchange authorization code for tokens"""
        try:
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "scope": "identify guilds guilds.join"
            }
            
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://discord.com/api/oauth2/token",
                    data=data,
                    headers=headers
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        logger.info("Code exchanged successfully")
                        return result
                    else:
                        error_text = await resp.text()
                        logger.error(f"Token exchange failed: {resp.status} - {error_text}")
                        
        except Exception as e:
            logger.error(f"Error exchanging code: {e}")
        
        return None
    
    async def get_user_info(self, access_token: str) -> Optional[Dict[str, Any]]:
        """Get user info from Discord"""
        try:
            headers = {"Authorization": f"Bearer {access_token}"}
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://discord.com/api/users/@me",
                    headers=headers
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        logger.error(f"Failed to get user info: {resp.status}")
                        
        except Exception as e:
            logger.error(f"Error getting user info: {e}")
        
        return None
    
    async def get_user_guilds(self, access_token: str) -> List[Dict[str, Any]]:
        """Get user's guilds from Discord"""
        try:
            headers = {"Authorization": f"Bearer {access_token}"}
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://discord.com/api/users/@me/guilds",
                    headers=headers
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                        
        except Exception as e:
            logger.error(f"Error getting user guilds: {e}")
        
        return []
    
    async def refresh_token(self, refresh_token: str) -> Optional[Dict[str, Any]]:
        """Refresh access token"""
        try:
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token
            }
            
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://discord.com/api/oauth2/token",
                    data=data,
                    headers=headers
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                        
        except Exception as e:
            logger.error(f"Error refreshing token: {e}")
        
        return None

# ==================== INITIALIZE ====================
db = DatabaseManager()
oauth = OAuthHandler()

# ==================== HELPER FUNCTIONS ====================
def create_jwt(user_data: Dict[str, Any], expires_days: int = 7) -> str:
    """Create JWT token"""
    payload = {
        "sub": user_data["id"],
        "username": user_data["username"],
        "avatar": user_data.get("avatar"),
        "email": user_data.get("email"),
        "exp": datetime.now() + timedelta(days=expires_days),
        "iat": datetime.now(),
        "type": "access"
    }
    return jwt.encode(payload, Config.JWT_SECRET, algorithm="HS256")

def verify_jwt(token: str) -> Optional[Dict[str, Any]]:
    """Verify JWT token"""
    try:
        payload = jwt.decode(token, Config.JWT_SECRET, algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError:
        logger.warning("JWT token expired")
    except jwt.InvalidTokenError as e:
        logger.error(f"Invalid JWT token: {e}")
    return None

async def verify_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Dict[str, Any]:
    """Dependency to verify JWT token"""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user_data = verify_jwt(credentials.credentials)
    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return user_data

def generate_verification_url(guild_id: str = None) -> Tuple[str, str]:
    """Generate verification URL and state"""
    state = secrets.token_urlsafe(32)
    
    params = {
        "client_id": oauth.client_id,
        "redirect_uri": oauth.redirect_uri,
        "response_type": "code",
        "scope": "identify guilds guilds.join",
        "state": state,
        "prompt": "none"
    }
    
    if guild_id:
        params["scope"] = "identify guilds guilds.join"
    
    verification_url = f"https://discord.com/api/oauth2/authorize?{urllib.parse.urlencode(params)}"
    
    return verification_url, state

# ==================== RATE LIMITING ====================
class RateLimiter:
    def __init__(self, requests_per_minute: int = 60):
        self.requests_per_minute = requests_per_minute
        self.requests = {}
    
    def is_allowed(self, key: str) -> bool:
        now = time.time()
        
        if key not in self.requests:
            self.requests[key] = []
        
        # Remove requests older than 1 minute
        self.requests[key] = [req_time for req_time in self.requests[key] if now - req_time < 60]
        
        if len(self.requests[key]) >= self.requests_per_minute:
            return False
        
        self.requests[key].append(now)
        return True

rate_limiter = RateLimiter()

# ==================== MIDDLEWARE ====================
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Rate limiting middleware"""
    client_ip = request.client.host
    path = request.url.path
    
    # Skip rate limiting for health check
    if path == "/health":
        return await call_next(request)
    
    if not rate_limiter.is_allowed(f"{client_ip}:{path}"):
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": "Too many requests"}
        )
    
    response = await call_next(request)
    return response

# ==================== ROUTES ====================

# Root endpoint
@app.get("/")
async def root():
    return JSONResponse({
        "status": "online",
        "service": "xotiicsverify API",
        "version": "4.0.0",
        "dashboard": True,
        "docs": "/docs" if os.getenv("ENVIRONMENT") == "development" else None,
        "endpoints": {
            "auth": "/api/auth/discord",
            "dashboard": "/api/dashboard/*",
            "bot": "/api/bot/*",
            "callback": "/oauth/callback",
            "health": "/health"
        }
    })

# Health check
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        # Check database connection
        supabase.table("verified_users").select("id").limit(1).execute()
        
        # Check Discord API
        async with aiohttp.ClientSession() as session:
            async with session.get("https://discord.com/api/v10/gateway") as resp:
                discord_status = resp.status == 200
        
        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "database": "connected",
            "discord_api": "reachable" if discord_status else "unreachable",
            "version": "4.0.0"
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service unhealthy"
        )

# Authentication endpoints
@app.get("/api/auth/discord")
async def discord_auth_endpoint(redirect_url: str = None):
    """Handle Discord authentication"""
    try:
        verification_url, state = generate_verification_url()
        
        db.save_oauth_state(
            state=state,
            redirect_url=redirect_url,
            type="dashboard_auth"
        )
        
        return RedirectResponse(verification_url)
        
    except Exception as e:
        logger.error(f"Discord auth error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )

@app.get("/api/auth/dashboard")
async def dashboard_auth(redirect_url: str = None):
    """Dashboard authentication (alias for /api/auth/discord)"""
    return await discord_auth_endpoint(redirect_url)

@app.get("/api/auth/callback")
async def auth_callback(code: str, state: str):
    """Handle dashboard login callback"""
    try:
        state_data = db.get_oauth_state(state)
        if not state_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired state"
            )
        
        token_data = await oauth.exchange_code(code)
        if not token_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to exchange code"
            )
        
        user_info = await oauth.get_user_info(token_data["access_token"])
        if not user_info:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to get user info"
            )
        
        # Get user guilds
        user_guilds = await oauth.get_user_guilds(token_data["access_token"])
        
        # Prepare user data
        user_data = {
            "id": user_info["id"],
            "username": f"{user_info['username']}#{user_info.get('discriminator', '0')}",
            "avatar": user_info.get("avatar"),
            "email": user_info.get("email"),
            "guilds": user_guilds
        }
        
        # Create JWT token
        jwt_token = create_jwt(user_data)
        
        # Redirect to frontend with token
        redirect_url = state_data.get("redirect_url", Config.FRONTEND_URL)
        return RedirectResponse(f"{redirect_url}?token={jwt_token}")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Auth callback error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )

# User verification endpoint
@app.get("/oauth/callback")
async def oauth_callback(code: str, state: str):
    """Handle OAuth callback for Discord server verification"""
    try:
        logger.info(f"Processing OAuth callback with state: {state[:10]}...")
        
        state_data = db.get_oauth_state(state)
        if not state_data:
            return HTMLResponse("""
                <html>
                    <head>
                        <title>Invalid Session</title>
                        <meta name="viewport" content="width=device-width, initial-scale=1.0">
                        <style>
                            body {
                                font-family: 'Segoe UI', sans-serif;
                                background: linear-gradient(135deg, #1a1a2e 0%, #0f0c29 100%);
                                color: white;
                                display: flex;
                                justify-content: center;
                                align-items: center;
                                height: 100vh;
                                margin: 0;
                                padding: 20px;
                            }
                            .container {
                                text-align: center;
                                background: rgba(255,0,0,0.1);
                                padding: 40px;
                                border-radius: 20px;
                                border: 2px solid #f72585;
                                max-width: 500px;
                                backdrop-filter: blur(10px);
                            }
                            h1 { margin-bottom: 20px; }
                            p { margin: 10px 0; }
                        </style>
                    </head>
                    <body>
                        <div class="container">
                            <h1>❌ Invalid Session</h1>
                            <p>Verification session expired or invalid.</p>
                            <p>Please try again from your Discord server.</p>
                        </div>
                    </body>
                </html>
            """, status_code=400)
        
        guild_id = state_data.get("guild_id")
        if not guild_id:
            return HTMLResponse("""
                <html>
                    <head>
                        <title>Missing Server</title>
                        <meta name="viewport" content="width=device-width, initial-scale=1.0">
                        <style>
                            body {
                                font-family: 'Segoe UI', sans-serif;
                                background: linear-gradient(135deg, #1a1a2e 0%, #0f0c29 100%);
                                color: white;
                                display: flex;
                                justify-content: center;
                                align-items: center;
                                height: 100vh;
                                margin: 0;
                                padding: 20px;
                            }
                            .container {
                                text-align: center;
                                background: rgba(255,165,0,0.1);
                                padding: 40px;
                                border-radius: 20px;
                                border: 2px solid #ffa500;
                                max-width: 500px;
                                backdrop-filter: blur(10px);
                            }
                            h1 { margin-bottom: 20px; }
                            p { margin: 10px 0; }
                        </style>
                    </head>
                    <body>
                        <div class="container">
                            <h1>⚠️ No Server Specified</h1>
                            <p>Please start verification from your Discord server.</p>
                        </div>
                    </body>
                </html>
            """, status_code=400)
        
        token_data = await oauth.exchange_code(code)
        if not token_data:
            return HTMLResponse("""
                <html>
                    <head>
                        <title>Authentication Failed</title>
                        <meta name="viewport" content="width=device-width, initial-scale=1.0">
                        <style>
                            body {
                                font-family: 'Segoe UI', sans-serif;
                                background: linear-gradient(135deg, #1a1a2e 0%, #0f0c29 100%);
                                color: white;
                                display: flex;
                                justify-content: center;
                                align-items: center;
                                height: 100vh;
                                margin: 0;
                                padding: 20px;
                            }
                            .container {
                                text-align: center;
                                background: rgba(255,0,0,0.1);
                                padding: 40px;
                                border-radius: 20px;
                                border: 2px solid #f72585;
                                max-width: 500px;
                                backdrop-filter: blur(10px);
                            }
                            h1 { margin-bottom: 20px; }
                            p { margin: 10px 0; }
                        </style>
                    </head>
                    <body>
                        <div class="container">
                            <h1>❌ Authentication Failed</h1>
                            <p>Could not verify your Discord account.</p>
                        </div>
                    </body>
                </html>
            """, status_code=400)
        
        user_info = await oauth.get_user_info(token_data["access_token"])
        if not user_info:
            return HTMLResponse("""
                <html>
                    <head>
                        <title>User Info Failed</title>
                        <meta name="viewport" content="width=device-width, initial-scale=1.0">
                        <style>
                            body {
                                font-family: 'Segoe UI', sans-serif;
                                background: linear-gradient(135deg, #1a1a2e 0%, #0f0c29 100%);
                                color: white;
                                display: flex;
                                justify-content: center;
                                align-items: center;
                                height: 100vh;
                                margin: 0;
                                padding: 20px;
                            }
                            .container {
                                text-align: center;
                                background: rgba(255,0,0,0.1);
                                padding: 40px;
                                border-radius: 20px;
                                border: 2px solid #f72585;
                                max-width: 500px;
                                backdrop-filter: blur(10px);
                            }
                            h1 { margin-bottom: 20px; }
                            p { margin: 10px 0; }
                        </style>
                    </head>
                    <body>
                        <div class="container">
                            <h1>❌ Could Not Get User Info</h1>
                            <p>Please try again.</p>
                        </div>
                    </body>
                </html>
            """, status_code=400)
        
        username = f"{user_info['username']}#{user_info.get('discriminator', '0')}"
        
        # Save user to database
        user_data = UserCreate(
            discord_id=user_info["id"],
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
        
        db.add_verified_user(user_data)
        
        # Add log entry
        db.add_log(
            log_type="verification",
            message=f"User {username} verified successfully",
            guild_id=guild_id,
            user_id=user_info["id"],
            metadata={"type": "verification_success"}
        )
        
        # Try to add user to guild using bot
        added_to_guild = False
        role_id = None
        
        if Config.DISCORD_BOT_TOKEN:
            headers = {
                "Authorization": f"Bot {Config.DISCORD_BOT_TOKEN}",
                "Content-Type": "application/json"
            }
            
            data = {"access_token": token_data["access_token"]}
            url = f"https://discord.com/api/v10/guilds/{guild_id}/members/{user_info['id']}"
            
            async with aiohttp.ClientSession() as session:
                async with session.put(url, headers=headers, json=data) as resp:
                    if resp.status in [200, 201, 204]:
                        logger.info(f"Added user {username} to guild {guild_id}")
                        added_to_guild = True
                        
                        # Try to get server config for default role
                        server_config = db.get_server_config(guild_id)
                        if server_config.get("verification_role"):
                            role_id = server_config["verification_role"]
                            
                            # Add role to user
                            role_url = f"https://discord.com/api/v10/guilds/{guild_id}/members/{user_info['id']}/roles/{role_id}"
                            async with session.put(role_url, headers=headers) as role_resp:
                                if role_resp.status in [200, 201, 204]:
                                    logger.info(f"Added role {role_id} to user {username}")
                        
                        # Mark as restored
                        db.mark_user_restored(user_info["id"], guild_id, role_id)
                        
                        # Add log
                        db.add_log(
                            log_type="restoration",
                            message=f"User {username} added to guild with role {role_id or 'no role'}",
                            guild_id=guild_id,
                            user_id=user_info["id"],
                            metadata={"role_id": role_id, "auto_added": True}
                        )
                    else:
                        logger.warning(f"Could not add user to guild: {resp.status}")
                        added_to_guild = False
        
        # Return success page
        success_html = f"""
        <html>
        <head>
            <title>Verification Successful</title>
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                body {{
                    font-family: 'Segoe UI', sans-serif;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                    padding: 20px;
                }}
                .container {{
                    text-align: center;
                    background: rgba(0, 0, 0, 0.3);
                    padding: 60px 40px;
                    border-radius: 20px;
                    backdrop-filter: blur(10px);
                    border: 2px solid #4ade80;
                    max-width: 500px;
                    width: 100%;
                }}
                h1 {{ font-size: 2.5em; margin: 0 0 20px 0; }}
                .success {{ color: #4ade80; font-weight: bold; font-size: 1.2em; margin: 10px 0; }}
                .info {{ color: #60a5fa; margin: 15px 0; }}
                .icon {{ font-size: 4em; margin-bottom: 20px; }}
                @media (max-width: 600px) {{
                    h1 {{ font-size: 2em; }}
                    .container {{ padding: 40px 20px; }}
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="icon">✅</div>
                <h1>Verification Successful!</h1>
                <p class="success">Welcome, {username}!</p>
                <p class="info">Your account has been verified and saved.</p>
                {"<p class='success'>✓ You have been added to the server!</p>" if added_to_guild else "<p class='info'>An admin will restore your access soon.</p>"}
                <p class="info">You can now close this window.</p>
            </div>
        </body>
        </html>
        """
        
        return HTMLResponse(success_html)
        
    except Exception as e:
        logger.error(f"OAuth callback error: {e}", exc_info=True)
        
        guild_id = state_data.get("guild_id") if 'state_data' in locals() else "unknown"
        db.add_log(
            log_type="error",
            message=f"Verification error: {str(e)}",
            guild_id=guild_id,
            metadata={"error": str(e), "traceback": str(e.__traceback__)}
        )
        
        return HTMLResponse("""
            <html>
                <head>
                    <title>Error</title>
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <style>
                        body {
                            font-family: 'Segoe UI', sans-serif;
                            background: linear-gradient(135deg, #1a1a2e 0%, #0f0c29 100%);
                            color: white;
                            display: flex;
                            justify-content: center;
                            align-items: center;
                            height: 100vh;
                            margin: 0;
                            padding: 20px;
                        }
                        .container {
                            text-align: center;
                            background: rgba(255,0,0,0.1);
                            padding: 40px;
                            border-radius: 20px;
                            border: 2px solid #f72585;
                            max-width: 500px;
                            backdrop-filter: blur(10px);
                        }
                        h1 { margin-bottom: 20px; }
                        p { margin: 10px 0; }
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>❌ An Error Occurred</h1>
                        <p>Something went wrong during verification.</p>
                        <p>Please try again from your Discord server.</p>
                    </div>
                </body>
            </html>
        """, status_code=500)

# ==================== DASHBOARD API ====================

# User endpoints
@app.get("/api/dashboard/user")
async def get_dashboard_user(user: Dict[str, Any] = Depends(verify_token)):
    """Get current user data for dashboard"""
    try:
        # Get additional user info from Discord
        user_guilds = await oauth.get_user_guilds(user.get("access_token", ""))
        
        return {
            "success": True,
            "user": {
                "id": user.get("sub"),
                "username": user.get("username"),
                "avatar": user.get("avatar"),
                "email": user.get("email"),
                "guilds": user_guilds
            }
        }
    except Exception as e:
        logger.error(f"Error getting dashboard user: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get user data"
        )

@app.get("/api/dashboard/servers")
async def get_user_servers(user: Dict[str, Any] = Depends(verify_token)):
    """Get user's guilds where bot is present"""
    try:
        # Get user guilds from Discord
        headers = {"Authorization": f"Bearer {user.get('access_token', '')}"}
        
        async with aiohttp.ClientSession() as session:
            # Get user guilds
            async with session.get(
                "https://discord.com/api/users/@me/guilds",
                headers=headers
            ) as resp:
                if resp.status != 200:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Failed to fetch user guilds"
                    )
                user_guilds = await resp.json()
            
            # Get bot guilds
            bot_headers = {"Authorization": f"Bot {Config.DISCORD_BOT_TOKEN}"}
            async with session.get(
                "https://discord.com/api/users/@me/guilds",
                headers=bot_headers
            ) as bot_resp:
                bot_guilds = await bot_resp.json() if bot_resp.status == 200 else []
        
        # Create set of bot guild IDs for fast lookup
        bot_guild_ids = {guild["id"] for guild in bot_guilds}
        
        servers = []
        for guild in user_guilds:
            guild_id = guild["id"]
            
            # Check if bot is in guild and user has admin permissions
            if guild_id in bot_guild_ids and (int(guild.get("permissions", 0)) & 0x8):  # ADMINISTRATOR permission
                # Get verified count from database
                verified_count = len(db.get_guild_users(guild_id, status="verified"))
                
                servers.append({
                    "id": guild_id,
                    "name": guild.get("name", "Unknown Server"),
                    "icon": guild.get("icon"),
                    "icon_url": f"https://cdn.discordapp.com/icons/{guild_id}/{guild['icon']}.png" if guild.get("icon") else None,
                    "member_count": guild.get("approximate_member_count", 0),
                    "verified_count": verified_count,
                    "owner": guild.get("owner", False),
                    "permissions": guild.get("permissions", 0)
                })
        
        return {
            "success": True,
            "servers": servers,
            "count": len(servers)
        }
        
    except Exception as e:
        logger.error(f"Error getting user servers: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get servers"
        )

# Server endpoints
@app.get("/api/dashboard/server/{guild_id}/stats")
async def get_server_stats(guild_id: str, user: Dict[str, Any] = Depends(verify_token)):
    """Get server statistics"""
    try:
        stats = db.get_guild_stats(guild_id)
        return {
            "success": True,
            "stats": stats,
            "guild_id": guild_id
        }
    except Exception as e:
        logger.error(f"Error getting server stats: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get server statistics"
        )

@app.get("/api/dashboard/server/{guild_id}/members")
async def get_server_members(
    guild_id: str,
    status: str = None,
    restored: bool = None,
    limit: int = 50,
    offset: int = 0,
    user: Dict[str, Any] = Depends(verify_token)
):
    """Get server members with pagination"""
    try:
        members = db.get_guild_users(
            guild_id,
            status=status,
            restored=restored,
            limit=limit,
            offset=offset
        )
        
        return {
            "success": True,
            "members": members,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "total": len(members),
                "has_more": len(members) == limit
            }
        }
    except Exception as e:
        logger.error(f"Error getting server members: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get members"
        )

@app.post("/api/dashboard/server/{guild_id}/restore")
async def restore_members(
    guild_id: str,
    request: Request,
    user: Dict[str, Any] = Depends(verify_token)
):
    """Restore members to server"""
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
        
        # Add log entry
        db.add_log(
            log_type="restoration",
            message=f"Restored {restored_count} members to server",
            guild_id=guild_id,
            user_id=user.get("sub"),
            metadata={
                "member_ids": member_ids,
                "role_id": role_id,
                "count": restored_count
            }
        )
        
        return {
            "success": True,
            "message": f"Restored {restored_count} members",
            "restored_count": restored_count
        }
        
    except Exception as e:
        logger.error(f"Error restoring members: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to restore members"
        )

@app.get("/api/dashboard/server/{guild_id}/verification-link")
async def get_verification_link(
    guild_id: str,
    user: Dict[str, Any] = Depends(verify_token)
):
    """Get verification URL for a specific guild"""
    try:
        # Generate verification URL
        verification_url, state = generate_verification_url(guild_id)
        
        # Save state with guild ID
        db.save_oauth_state(
            state=state,
            guild_id=guild_id,
            user_id=user.get("sub"),
            type="verification",
            metadata={
                "generated_by": user.get("username"),
                "timestamp": datetime.now().isoformat()
            }
        )
        
        # Generate QR code
        qr_code_url = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={urllib.parse.quote(verification_url)}"
        
        return {
            "success": True,
            "verification_url": verification_url,
            "qr_code_url": qr_code_url,
            "embed_code": f"[Verify Here]({verification_url})",
            "state": state,
            "expires_in": "10 minutes"
        }
        
    except Exception as e:
        logger.error(f"Error generating verification link: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate verification link"
        )

# Configuration endpoints
@app.get("/api/dashboard/server/{guild_id}/config")
async def get_server_config(
    guild_id: str,
    user: Dict[str, Any] = Depends(verify_token)
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
        logger.error(f"Error getting server config: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get server configuration"
        )

@app.post("/api/dashboard/server/{guild_id}/config")
async def update_server_config(
    guild_id: str,
    request: Request,
    user: Dict[str, Any] = Depends(verify_token)
):
    """Update server configuration"""
    try:
        data = await request.json()
        
        # Validate config
        config = ServerConfig(**data)
        
        # Save to database
        db.save_server_config(guild_id, config.dict())
        
        # Add log entry
        db.add_log(
            log_type="config",
            message="Server configuration updated",
            guild_id=guild_id,
            user_id=user.get("sub"),
            metadata={"config": data}
        )
        
        return {
            "success": True,
            "message": "Configuration saved",
            "config": config.dict()
        }
        
    except Exception as e:
        logger.error(f"Error saving server config: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid configuration: {str(e)}"
        )

@app.get("/api/dashboard/bot/config")
async def get_bot_config(user: Dict[str, Any] = Depends(verify_token)):
    """Get bot configuration"""
    try:
        config = db.get_bot_config(user.get("sub"))
        return {
            "success": True,
            "config": config
        }
    except Exception as e:
        logger.error(f"Error getting bot config: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get bot configuration"
        )

@app.post("/api/dashboard/bot/config")
async def update_bot_config(
    request: Request,
    user: Dict[str, Any] = Depends(verify_token)
):
    """Update bot configuration"""
    try:
        data = await request.json()
        
        # Validate config
        config = BotConfig(**data)
        
        # Save to database
        db.save_bot_config(user.get("sub"), config.dict())
        
        # Add log entry
        db.add_log(
            log_type="config",
            message="Bot configuration updated",
            user_id=user.get("sub"),
            metadata={"config": data}
        )
        
        return {
            "success": True,
            "message": "Bot configuration saved",
            "config": config.dict()
        }
        
    except Exception as e:
        logger.error(f"Error saving bot config: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid configuration: {str(e)}"
        )

# Logs endpoint
@app.get("/api/dashboard/server/{guild_id}/logs")
async def get_server_logs(
    guild_id: str,
    log_type: str = None,
    limit: int = 100,
    user: Dict[str, Any] = Depends(verify_token)
):
    """Get server logs"""
    try:
        logs = db.get_logs(guild_id, log_type=log_type, limit=limit)
        return {
            "success": True,
            "logs": logs,
            "count": len(logs)
        }
    except Exception as e:
        logger.error(f"Error getting logs: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get logs"
        )

# ==================== BOT API ====================

@app.get("/api/bot/status")
async def get_bot_status():
    """Get bot status"""
    try:
        # Check Discord API connectivity
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bot {Config.DISCORD_BOT_TOKEN}"}
            async with session.get(
                "https://discord.com/api/v10/gateway/bot",
                headers=headers
            ) as resp:
                if resp.status == 200:
                    discord_data = await resp.json()
                    discord_status = "connected"
                else:
                    discord_status = "disconnected"
                    discord_data = {}
        
        # Get database stats
        try:
            total_users = supabase.table("verified_users")\
                .select("id", count="exact")\
                .execute()
            total_users_count = total_users.count or 0
        except:
            total_users_count = 0
        
        return {
            "status": "online",
            "timestamp": datetime.now().isoformat(),
            "version": "4.0.0",
            "discord": discord_status,
            "database": "connected",
            "stats": {
                "total_users": total_users_count,
                "shards": discord_data.get("shards", 1),
                "session_start_limit": discord_data.get("session_start_limit", {})
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting bot status: {e}")
        return {
            "status": "error",
            "timestamp": datetime.now().isoformat(),
            "version": "4.0.0",
            "error": str(e)
        }

@app.get("/api/bot/guild/{guild_id}/verified")
async def get_guild_verified_users(guild_id: str):
    """Get verified users for a guild (for bot restoration)"""
    try:
        users = db.get_guild_users(guild_id, status="verified", restored=False)
        return {
            "success": True,
            "users": users,
            "count": len(users)
        }
    except Exception as e:
        logger.error(f"Error getting guild verified users: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get verified users"
        )

@app.post("/api/bot/guild/{guild_id}/restore")
async def bot_restore_members(guild_id: str, request: Request):
    """Bot endpoint to restore members"""
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
        
        # Add log entry
        db.add_log(
            log_type="restoration",
            message=f"Bot restored {restored_count} members",
            guild_id=guild_id,
            metadata={
                "member_ids": member_ids,
                "role_id": role_id,
                "count": restored_count,
                "source": "bot_api"
            }
        )
        
        return {
            "success": True,
            "restored_count": restored_count,
            "message": f"Restored {restored_count} members"
        }
        
    except Exception as e:
        logger.error(f"Bot restore error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Restoration failed"
        )

# ==================== UTILITY ENDPOINTS ====================

@app.get("/api/verify/{guild_id}")
async def get_verification_url_public(guild_id: str):
    """Get verification URL for a specific guild (public)"""
    try:
        verification_url, state = generate_verification_url(guild_id)
        
        # Save state with guild ID
        db.save_oauth_state(
            state=state,
            guild_id=guild_id,
            type="public_verification"
        )
        
        return {
            "success": True,
            "verification_url": verification_url,
            "embed_code": f"[Verify Here]({verification_url})",
            "qr_code": f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={urllib.parse.quote(verification_url)}"
        }
        
    except Exception as e:
        logger.error(f"Error generating public verification link: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate verification link"
        )

# ==================== ERROR HANDLERS ====================

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions"""
    logger.warning(f"HTTPException: {exc.detail} - {request.url}")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": exc.detail,
            "path": request.url.path,
            "timestamp": datetime.now().isoformat()
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle general exceptions"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "error": "Internal server error",
            "path": request.url.path,
            "timestamp": datetime.now().isoformat(),
            "request_id": str(uuid.uuid4())
        }
    )

# ==================== START APPLICATION ====================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=os.getenv("ENVIRONMENT") == "development",
        log_level="info",
        access_log=True
    )
