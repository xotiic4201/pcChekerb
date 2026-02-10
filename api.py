from fastapi import FastAPI, HTTPException, Request, Depends, status, BackgroundTasks
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from contextlib import asynccontextmanager
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
import time
import uuid
import asyncio

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
    TRANSFER_BATCH_SIZE = int(os.getenv("TRANSFER_BATCH_SIZE", 50))
    MAX_TRANSFER_USERS = int(os.getenv("MAX_TRANSFER_USERS", 1000))

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
    
    # Verify database connection
    try:
        supabase.table("verified_users").select("id").limit(1).execute()
        logger.info("Database connection verified")
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise
    
    # Initialize tables if they don't exist
    await initialize_tables()
    
    yield
    
    # Shutdown
    logger.info("Shutting down xotiicsverify API...")

async def initialize_tables():
    """Initialize database tables if they don't exist"""
    try:
        # Create verified_users table if not exists
        supabase.rpc('create_table_if_not_exists', {
            'table_name': 'verified_users',
            'table_schema': '''
                id BIGSERIAL PRIMARY KEY,
                discord_id TEXT NOT NULL,
                username TEXT NOT NULL,
                access_token TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
                guild_id TEXT NOT NULL,
                metadata JSONB DEFAULT '{}'::jsonb,
                status TEXT DEFAULT 'verified',
                verified_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                restored BOOLEAN DEFAULT FALSE,
                restored_at TIMESTAMP WITH TIME ZONE,
                restored_role_id TEXT,
                transferred_from TEXT,
                transferred_at TIMESTAMP WITH TIME ZONE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                UNIQUE(discord_id, guild_id)
            '''
        }).execute()
        
        # Create indexes
        supabase.rpc('create_index_if_not_exists', {
            'table_name': 'verified_users',
            'index_name': 'idx_verified_users_guild_status',
            'index_sql': 'CREATE INDEX IF NOT EXISTS idx_verified_users_guild_status ON verified_users(guild_id, status)'
        }).execute()
        
        supabase.rpc('create_index_if_not_exists', {
            'table_name': 'verified_users',
            'index_name': 'idx_verified_users_discord_id',
            'index_sql': 'CREATE INDEX IF NOT EXISTS idx_verified_users_discord_id ON verified_users(discord_id)'
        }).execute()
        
        # Create oauth_states table
        supabase.rpc('create_table_if_not_exists', {
            'table_name': 'oauth_states',
            'table_schema': '''
                id BIGSERIAL PRIMARY KEY,
                state TEXT NOT NULL UNIQUE,
                user_id TEXT,
                guild_id TEXT,
                redirect_url TEXT,
                type TEXT DEFAULT 'auth',
                metadata JSONB DEFAULT '{}'::jsonb,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                expires_at TIMESTAMP WITH TIME ZONE NOT NULL
            '''
        }).execute()
        
        # Create bot_configs table
        supabase.rpc('create_table_if_not_exists', {
            'table_name': 'bot_configs',
            'table_schema': '''
                id BIGSERIAL PRIMARY KEY,
                user_id TEXT NOT NULL UNIQUE,
                config JSONB DEFAULT '{}'::jsonb,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            '''
        }).execute()
        
        # Create server_configs table
        supabase.rpc('create_table_if_not_exists', {
            'table_name': 'server_configs',
            'table_schema': '''
                id BIGSERIAL PRIMARY KEY,
                guild_id TEXT NOT NULL UNIQUE,
                config JSONB DEFAULT '{}'::jsonb,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            '''
        }).execute()
        
        # Create logs table
        supabase.rpc('create_table_if_not_exists', {
            'table_name': 'logs',
            'table_schema': '''
                id BIGSERIAL PRIMARY KEY,
                guild_id TEXT,
                type TEXT NOT NULL,
                message TEXT NOT NULL,
                user_id TEXT,
                metadata JSONB DEFAULT '{}'::jsonb,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            '''
        }).execute()
        
        # Create transfer_jobs table
        supabase.rpc('create_table_if_not_exists', {
            'table_name': 'transfer_jobs',
            'table_schema': '''
                id BIGSERIAL PRIMARY KEY,
                job_id TEXT NOT NULL UNIQUE,
                source_guild_id TEXT NOT NULL,
                target_guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                config JSONB DEFAULT '{}'::jsonb,
                result JSONB DEFAULT '{}'::jsonb,
                total_users INTEGER DEFAULT 0,
                processed_users INTEGER DEFAULT 0,
                transferred_users INTEGER DEFAULT 0,
                failed_users INTEGER DEFAULT 0,
                started_at TIMESTAMP WITH TIME ZONE,
                completed_at TIMESTAMP WITH TIME ZONE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            '''
        }).execute()
        
        logger.info("Database tables initialized")
        
    except Exception as e:
        logger.warning(f"Could not initialize tables (they may already exist): {e}")

# ==================== FASTAPI APP ====================
app = FastAPI(
    title="xotiicsverify API",
    description="Secure Discord verification system backend with dashboard",
    version="5.0.0",
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
    max_transfer_users: int = Field(1000, ge=1, le=5000)

class ServerConfig(BaseModel):
    verification_channel: Optional[str] = None
    verification_role: Optional[str] = None
    welcome_message: Optional[str] = None
    enable_auto_verification: bool = True
    log_channel: Optional[str] = None
    admin_roles: List[str] = []
    allow_user_transfers: bool = True
    auto_approve_transfers: bool = False

class UserCreate(BaseModel):
    discord_id: str
    username: str
    access_token: str
    refresh_token: str
    expires_in: int
    guild_id: str
    metadata: Dict[str, Any] = {}

class TransferRequest(BaseModel):
    source_guild_id: str
    target_guild_id: str
    user_ids: List[str] = []
    limit: Optional[int] = Field(None, ge=1, le=1000)
    assign_role_id: Optional[str] = None
    remove_from_source: bool = False
    transfer_all: bool = False

class TransferJob(BaseModel):
    job_id: str
    source_guild_id: str
    target_guild_id: str
    user_id: str
    config: Dict[str, Any]
    status: str = "pending"
    result: Dict[str, Any] = {}

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
                "metadata": kwargs.get("metadata", {}),
                "created_at": datetime.now().isoformat(),
                "expires_at": (datetime.now() + timedelta(minutes=10)).isoformat()
            }
            
            # Clean expired states first
            try:
                supabase.table("oauth_states")\
                    .delete()\
                    .lt("expires_at", datetime.now().isoformat())\
                    .execute()
            except:
                pass  # Table might not exist yet
            
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
                # Update existing
                supabase.table("verified_users")\
                    .update(data)\
                    .eq("discord_id", user_data.discord_id)\
                    .eq("guild_id", user_data.guild_id)\
                    .execute()
                logger.info(f"User updated: {user_data.username} ({user_data.discord_id})")
            else:
                # Insert new
                data["created_at"] = datetime.now().isoformat()
                supabase.table("verified_users").insert(data).execute()
                logger.info(f"User added: {user_data.username} ({user_data.discord_id})")
            
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
                try:
                    access_token = self._decrypt(user["access_token"])
                    refresh_token = self._decrypt(user["refresh_token"])
                except:
                    # If decryption fails, return without tokens
                    access_token = ""
                    refresh_token = ""
                
                return {
                    "discord_id": user["discord_id"],
                    "username": user["username"],
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "expires_at": datetime.fromisoformat(user["expires_at"].replace("Z", "+00:00")) if user.get("expires_at") else None,
                    "guild_id": user["guild_id"],
                    "metadata": user.get("metadata", {}),
                    "verified_at": datetime.fromisoformat(user["verified_at"].replace("Z", "+00:00")) if user.get("verified_at") else None,
                    "restored": user.get("restored", False),
                    "restored_at": datetime.fromisoformat(user["restored_at"].replace("Z", "+00:00")) if user.get("restored_at") else None,
                    "restored_role_id": user.get("restored_role_id"),
                    "status": user.get("status", "pending"),
                    "transferred_from": user.get("transferred_from"),
                    "transferred_at": datetime.fromisoformat(user["transferred_at"].replace("Z", "+00:00")) if user.get("transferred_at") else None
                }
                
        except Exception as e:
            logger.error(f"Error getting user: {e}")
        
        return None
    
    def get_users_by_guild(self, guild_id: str, **kwargs) -> List[Dict[str, Any]]:
        """Get users for a guild with filtering"""
        try:
            query = supabase.table("verified_users")\
                .select("discord_id, username, guild_id, verified_at, restored, restored_at, restored_role_id, status, metadata, transferred_from, transferred_at")\
                .eq("guild_id", guild_id)\
                .order("verified_at", desc=True)
            
            # Apply filters
            if kwargs.get("status") and kwargs["status"] != "all":
                query = query.eq("status", kwargs["status"])
            
            if kwargs.get("restored") is not None:
                query = query.eq("restored", kwargs["restored"])
            
            if kwargs.get("transferred") is not None:
                if kwargs["transferred"]:
                    query = query.not_.is_("transferred_from", "null")
                else:
                    query = query.is_("transferred_from", "null")
            
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
                    "metadata": user.get("metadata", {}),
                    "transferred_from": user.get("transferred_from"),
                    "transferred_at": user.get("transferred_at")
                })
            
            return users
            
        except Exception as e:
            logger.error(f"Error getting guild users: {e}")
            return []
    
    def get_guild_users_count(self, guild_id: str, **kwargs) -> int:
        """Get count of users for a guild with filtering"""
        try:
            query = supabase.table("verified_users")\
                .select("id", count="exact")\
                .eq("guild_id", guild_id)
            
            if kwargs.get("status") and kwargs["status"] != "all":
                query = query.eq("status", kwargs["status"])
            
            if kwargs.get("restored") is not None:
                query = query.eq("restored", kwargs["restored"])
            
            response = query.execute()
            return response.count or 0
            
        except Exception as e:
            logger.error(f"Error getting guild users count: {e}")
            return 0
    
    def get_guild_stats(self, guild_id: str) -> Dict[str, Any]:
        """Get statistics for a guild"""
        try:
            # Total verified
            total = self.get_guild_users_count(guild_id)
            
            # Restored
            restored = self.get_guild_users_count(guild_id, restored=True)
            
            # Pending (not restored)
            pending = self.get_guild_users_count(guild_id, restored=False)
            
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
            
            # Transferred users
            transferred_resp = supabase.table("verified_users")\
                .select("id", count="exact")\
                .eq("guild_id", guild_id)\
                .not_.is_("transferred_from", "null")\
                .execute()
            transferred_count = transferred_resp.count or 0
            
            return {
                "total_verified": total,
                "restored": restored,
                "pending": pending,
                "verified_today": today_count,
                "verified_week": week_count,
                "transferred_users": transferred_count
            }
            
        except Exception as e:
            logger.error(f"Error getting guild stats: {e}")
            return {
                "total_verified": 0,
                "restored": 0,
                "pending": 0,
                "verified_today": 0,
                "verified_week": 0,
                "transferred_users": 0
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
    
    # User Transfer Methods
    def transfer_user(self, discord_id: str, source_guild_id: str, target_guild_id: str, **kwargs) -> bool:
        """Transfer a user from one guild to another"""
        try:
            # Get user from source guild
            user = self.get_user(discord_id, source_guild_id)
            if not user:
                logger.error(f"User {discord_id} not found in source guild {source_guild_id}")
                return False
            
            # Check if user already exists in target guild
            existing = self.get_user(discord_id, target_guild_id)
            if existing:
                logger.warning(f"User {discord_id} already exists in target guild {target_guild_id}")
                # Update existing user with transfer info
                update_data = {
                    "transferred_from": source_guild_id,
                    "transferred_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat()
                }
                
                if kwargs.get("metadata"):
                    update_data["metadata"] = {**existing.get("metadata", {}), **kwargs["metadata"]}
                
                supabase.table("verified_users")\
                    .update(update_data)\
                    .eq("discord_id", discord_id)\
                    .eq("guild_id", target_guild_id)\
                    .execute()
                return True
            
            # Prepare new user data for target guild
            user_data = UserCreate(
                discord_id=discord_id,
                username=user["username"],
                access_token=user["access_token"] or "",  # Note: May need to refresh token
                refresh_token=user["refresh_token"] or "",
                expires_in=int((user["expires_at"] - datetime.now()).total_seconds()) if user.get("expires_at") else 604800,
                guild_id=target_guild_id,
                metadata={
                    **user.get("metadata", {}),
                    "transferred_from": source_guild_id,
                    "transferred_at": datetime.now().isoformat(),
                    "transfer_metadata": kwargs.get("metadata", {})
                }
            )
            
            # Add user to target guild
            success = self.add_verified_user(user_data)
            
            if success and kwargs.get("remove_from_source"):
                # Remove from source guild if requested
                supabase.table("verified_users")\
                    .delete()\
                    .eq("discord_id", discord_id)\
                    .eq("guild_id", source_guild_id)\
                    .execute()
                logger.info(f"Removed user {discord_id} from source guild {source_guild_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error transferring user {discord_id}: {e}")
            return False
    
    def get_users_for_transfer(self, source_guild_id: str, **kwargs) -> List[Dict[str, Any]]:
        """Get users available for transfer from a guild"""
        try:
            query = supabase.table("verified_users")\
                .select("discord_id, username, verified_at, restored, status, metadata")\
                .eq("guild_id", source_guild_id)\
                .order("verified_at", desc=True)
            
            # Filter by status if specified
            if kwargs.get("status"):
                query = query.eq("status", kwargs["status"])
            
            # Filter by restored status if specified
            if kwargs.get("restored") is not None:
                query = query.eq("restored", kwargs["restored"])
            
            # Apply limit
            limit = kwargs.get("limit", Config.MAX_TRANSFER_USERS)
            query = query.limit(min(limit, Config.MAX_TRANSFER_USERS))
            
            response = query.execute()
            
            users = []
            for user in response.data:
                users.append({
                    "discord_id": user["discord_id"],
                    "username": user["username"],
                    "verified_at": user.get("verified_at"),
                    "restored": user.get("restored", False),
                    "status": user.get("status", "verified"),
                    "metadata": user.get("metadata", {})
                })
            
            return users
            
        except Exception as e:
            logger.error(f"Error getting users for transfer: {e}")
            return []
    
    # Transfer Job Management
    def create_transfer_job(self, job_data: TransferJob) -> bool:
        """Create a new transfer job"""
        try:
            data = {
                "job_id": job_data.job_id,
                "source_guild_id": job_data.source_guild_id,
                "target_guild_id": job_data.target_guild_id,
                "user_id": job_data.user_id,
                "status": job_data.status,
                "config": job_data.config,
                "result": job_data.result,
                "created_at": datetime.now().isoformat()
            }
            
            supabase.table("transfer_jobs").insert(data).execute()
            logger.info(f"Created transfer job: {job_data.job_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error creating transfer job: {e}")
            return False
    
    def update_transfer_job(self, job_id: str, **kwargs) -> bool:
        """Update a transfer job"""
        try:
            update_data = {"updated_at": datetime.now().isoformat()}
            
            if "status" in kwargs:
                update_data["status"] = kwargs["status"]
            
            if "result" in kwargs:
                update_data["result"] = kwargs["result"]
            
            if "total_users" in kwargs:
                update_data["total_users"] = kwargs["total_users"]
            
            if "processed_users" in kwargs:
                update_data["processed_users"] = kwargs["processed_users"]
            
            if "transferred_users" in kwargs:
                update_data["transferred_users"] = kwargs["transferred_users"]
            
            if "failed_users" in kwargs:
                update_data["failed_users"] = kwargs["failed_users"]
            
            if "started_at" in kwargs:
                update_data["started_at"] = kwargs["started_at"]
            
            if "completed_at" in kwargs:
                update_data["completed_at"] = kwargs["completed_at"]
            
            supabase.table("transfer_jobs")\
                .update(update_data)\
                .eq("job_id", job_id)\
                .execute()
            
            return True
            
        except Exception as e:
            logger.error(f"Error updating transfer job: {e}")
            return False
    
    def get_transfer_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get a transfer job by ID"""
        try:
            response = supabase.table("transfer_jobs")\
                .select("*")\
                .eq("job_id", job_id)\
                .execute()
            
            if response.data:
                return response.data[0]
                
        except Exception as e:
            logger.error(f"Error getting transfer job: {e}")
        
        return None
    
    def get_user_transfer_jobs(self, user_id: str, guild_id: str = None, limit: int = 10) -> List[Dict[str, Any]]:
        """Get transfer jobs for a user"""
        try:
            query = supabase.table("transfer_jobs")\
                .select("*")\
                .eq("user_id", user_id)\
                .order("created_at", desc=True)\
                .limit(limit)
            
            if guild_id:
                query = query.or_(f"source_guild_id.eq.{guild_id},target_guild_id.eq.{guild_id}")
            
            response = query.execute()
            return response.data
            
        except Exception as e:
            logger.error(f"Error getting user transfer jobs: {e}")
            return []
    
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
        self.bot_token = Config.DISCORD_BOT_TOKEN
    
    def get_authorization_url(self, redirect_uri: str, state: str, guild_id: str = None) -> str:
        """Generate Discord authorization URL"""
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
            "prompt": "none"
        }
        
        if guild_id:
            # For user verification - need guilds.join permission
            params["scope"] = "identify guilds guilds.join"
        else:
            # For dashboard login - only need identify and guilds
            params["scope"] = "identify guilds"
        
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
    
    async def get_bot_guilds(self) -> List[Dict[str, Any]]:
        """Get guilds the bot is in"""
        try:
            headers = {"Authorization": f"Bot {self.bot_token}"}
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://discord.com/api/users/@me/guilds",
                    headers=headers
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                        
        except Exception as e:
            logger.error(f"Error getting bot guilds: {e}")
        
        return []

# ==================== INITIALIZE ====================
db = DatabaseManager()
oauth = OAuthHandler()

# ==================== HELPER FUNCTIONS ====================
def create_jwt(user_data: Dict[str, Any], expires_days: int = 7) -> str:
    """Create JWT token"""
    payload = {
        "sub": user_data.get("id"),
        "username": user_data.get("username"),
        "avatar": user_data.get("avatar"),
        "email": user_data.get("email"),
        "access_token": user_data.get("access_token", ""),  # Store for API calls
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

# ==================== TRANSFER MANAGER ====================
class TransferManager:
    def __init__(self):
        self.active_transfers = {}
    
    async def process_transfer(self, job_id: str, source_guild_id: str, target_guild_id: str, 
                               user_ids: List[str], user_data: Dict[str, Any], config: Dict[str, Any]):
        """Process a user transfer"""
        try:
            db.update_transfer_job(job_id, 
                status="processing",
                started_at=datetime.now().isoformat(),
                total_users=len(user_ids)
            )
            
            transferred = []
            failed = []
            
            for i, discord_id in enumerate(user_ids):
                try:
                    # Get user data for transfer
                    metadata = {
                        "transferred_by": user_data.get("username"),
                        "transferred_by_id": user_data.get("sub"),
                        "assign_role_id": config.get("assign_role_id"),
                        "remove_from_source": config.get("remove_from_source", False)
                    }
                    
                    # Transfer user
                    success = db.transfer_user(
                        discord_id=discord_id,
                        source_guild_id=source_guild_id,
                        target_guild_id=target_guild_id,
                        metadata=metadata,
                        remove_from_source=config.get("remove_from_source", False)
                    )
                    
                    if success:
                        transferred.append(discord_id)
                    else:
                        failed.append(discord_id)
                    
                    # Update progress
                    if (i + 1) % Config.TRANSFER_BATCH_SIZE == 0 or (i + 1) == len(user_ids):
                        db.update_transfer_job(job_id,
                            processed_users=i + 1,
                            transferred_users=len(transferred),
                            failed_users=len(failed)
                        )
                    
                    # Rate limiting
                    await asyncio.sleep(0.1)
                    
                except Exception as e:
                    logger.error(f"Error transferring user {discord_id}: {e}")
                    failed.append(discord_id)
            
            # Mark job as completed
            db.update_transfer_job(job_id,
                status="completed",
                completed_at=datetime.now().isoformat(),
                result={
                    "transferred": transferred,
                    "failed": failed,
                    "total_transferred": len(transferred),
                    "total_failed": len(failed)
                }
            )
            
            # Add log entry
            db.add_log(
                log_type="transfer",
                message=f"Transfer completed: {len(transferred)} users transferred from {source_guild_id} to {target_guild_id}",
                guild_id=target_guild_id,
                user_id=user_data.get("sub"),
                metadata={
                    "job_id": job_id,
                    "source_guild": source_guild_id,
                    "target_guild": target_guild_id,
                    "transferred_count": len(transferred),
                    "failed_count": len(failed)
                }
            )
            
            logger.info(f"Transfer job {job_id} completed: {len(transferred)} transferred, {len(failed)} failed")
            
        except Exception as e:
            logger.error(f"Transfer job {job_id} failed: {e}")
            db.update_transfer_job(job_id,
                status="failed",
                completed_at=datetime.now().isoformat(),
                result={"error": str(e)}
            )
            
            db.add_log(
                log_type="error",
                message=f"Transfer job failed: {str(e)}",
                guild_id=target_guild_id,
                user_id=user_data.get("sub"),
                metadata={
                    "job_id": job_id,
                    "error": str(e)
                }
            )

transfer_manager = TransferManager()

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
        "version": "5.0.0",
        "dashboard": True,
        "features": ["verification", "user_transfer", "dashboard", "bot_integration"],
        "docs": "/docs" if os.getenv("ENVIRONMENT") == "development" else None,
        "endpoints": {
            "auth": "/api/auth/discord",
            "dashboard": "/api/dashboard/*",
            "bot": "/api/bot/*",
            "transfer": "/api/transfer/*",
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
        discord_status = False
        if Config.DISCORD_BOT_TOKEN:
            try:
                async with aiohttp.ClientSession() as session:
                    headers = {"Authorization": f"Bot {Config.DISCORD_BOT_TOKEN}"}
                    async with session.get("https://discord.com/api/v10/gateway/bot", headers=headers) as resp:
                        discord_status = resp.status == 200
            except:
                pass
        
        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "database": "connected",
            "discord_api": "reachable" if discord_status else "unreachable",
            "version": "5.0.0",
            "active_transfers": len(transfer_manager.active_transfers)
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service unhealthy"
        )

# ==================== AUTHENTICATION ENDPOINTS ====================

@app.get("/api/auth/discord")
async def discord_auth_endpoint(redirect_url: str = None):
    """Handle Discord authentication for dashboard"""
    try:
        state = secrets.token_urlsafe(32)
        
        # Save state for dashboard authentication
        db.save_oauth_state(
            state=state,
            redirect_url=redirect_url,
            type="dashboard_auth"
        )
        
        # Use the API callback for dashboard login
        redirect_uri = f"{Config.API_URL}/api/auth/callback"
        auth_url = oauth.get_authorization_url(redirect_uri, state)
        
        return RedirectResponse(auth_url)
        
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
        logger.info(f"Processing dashboard callback with state: {state[:10]}...")
        
        state_data = db.get_oauth_state(state)
        if not state_data:
            logger.error(f"Invalid state for dashboard callback: {state}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired state"
            )
        
        # Verify this is a dashboard auth request
        if state_data.get("type") != "dashboard_auth":
            logger.error(f"Invalid auth type for dashboard: {state_data.get('type')}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid authentication type"
            )
        
        redirect_uri = f"{Config.API_URL}/api/auth/callback"
        token_data = await oauth.exchange_code(code, redirect_uri)
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
            "access_token": token_data["access_token"],  # Store for future API calls
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
        logger.error(f"Auth callback error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )

# ==================== USER VERIFICATION ENDPOINTS ====================

@app.get("/api/verify/{guild_id}")
async def start_verification(guild_id: str):
    """Start verification process for a specific guild"""
    try:
        state = secrets.token_urlsafe(32)
        
        # Save state with guild ID for verification
        db.save_oauth_state(
            state=state,
            guild_id=guild_id,
            type="verification"
        )
        
        # Use the user verification callback
        redirect_uri = f"{Config.API_URL}/oauth/callback"
        auth_url = oauth.get_authorization_url(redirect_uri, state, guild_id)
        
        return {
            "success": True,
            "verification_url": auth_url,
            "embed_code": f"[Verify Here]({auth_url})",
            "guild_id": guild_id
        }
        
    except Exception as e:
        logger.error(f"Error starting verification: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start verification"
        )

@app.get("/oauth/callback")
async def oauth_callback(code: str, state: str):
    """Handle OAuth callback for Discord server verification"""
    try:
        logger.info(f"Processing OAuth callback with state: {state[:10]}...")
        
        state_data = db.get_oauth_state(state)
        if not state_data:
            logger.error(f"Invalid state for OAuth callback: {state}")
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
        
        # Verify this is a verification request
        if state_data.get("type") != "verification":
            logger.error(f"Invalid auth type for verification: {state_data.get('type')}")
            return HTMLResponse("""
                <html>
                    <head>
                        <title>Invalid Request</title>
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
                            <h1>⚠️ Invalid Request</h1>
                            <p>This verification link is invalid.</p>
                            <p>Please use the correct verification link from your Discord server.</p>
                        </div>
                    </body>
                </html>
            """, status_code=400)
        
        guild_id = state_data.get("guild_id")
        if not guild_id:
            logger.error("No guild_id in verification state")
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
        
        redirect_uri = f"{Config.API_URL}/oauth/callback"
        token_data = await oauth.exchange_code(code, redirect_uri)
        if not token_data:
            logger.error("Failed to exchange code for verification")
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
            logger.error("Failed to get user info for verification")
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
                "verified": user_info.get("verified", False),
                "mfa_enabled": user_info.get("mfa_enabled", False)
            }
        )
        
        success = db.add_verified_user(user_data)
        
        if not success:
            logger.error("Failed to save user to database")
            return HTMLResponse("""
                <html>
                    <head>
                        <title>Database Error</title>
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
                            <h1>❌ Database Error</h1>
                            <p>Failed to save verification data.</p>
                            <p>Please try again later.</p>
                        </div>
                    </body>
                </html>
            """, status_code=500)
        
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
                try:
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
                except Exception as e:
                    logger.error(f"Error adding user to guild: {e}")
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
            metadata={"error": str(e)}
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

# ==================== MISSING BOT ENDPOINTS ====================

@app.post("/api/bot/verify-manual")
async def manual_verification(request: Request):
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
        logger.error(f"Manual verification error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid request: {str(e)}"
        )

@app.get("/api/bot/guild/{guild_id}/verified")
async def get_guild_verified_users_simple(guild_id: str):
    """Get verified users for a guild (simplified for bot)"""
    try:
        users = db.get_users_by_guild(guild_id, status="verified")
        
        # Format for bot response
        formatted_users = []
        for user in users:
            formatted_users.append({
                "discord_id": user["discord_id"],
                "username": user["username"],
                "restored": user.get("restored", False),
                "verified_at": user.get("verified_at"),
                "status": user.get("status", "verified")
            })
        
        return {
            "success": True,
            "users": formatted_users,
            "count": len(formatted_users)
        }
    except Exception as e:
        logger.error(f"Error getting guild verified users: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get verified users"
        )

@app.post("/api/bot/guild/{guild_id}/send-verification")
async def bot_send_verification_embed(
    guild_id: str,
    request: Request
):
    """Bot endpoint to send verification embed"""
    try:
        data = await request.json()
        channel_id = data.get("channel_id")
        
        if not channel_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Channel ID is required"
            )
        
        # Generate verification URL
        state = secrets.token_urlsafe(32)
        verification_url = f"{Config.API_URL}/verify/{guild_id}"
        
        # Save state
        db.save_oauth_state(
            state=state,
            guild_id=guild_id,
            type="verification",
            metadata={"channel_id": channel_id}
        )
        
        # Add log
        db.add_log(
            log_type="verification",
            message=f"Verification embed generated for channel {channel_id}",
            guild_id=guild_id,
            metadata={
                "channel_id": channel_id,
                "verification_url": verification_url
            }
        )
        
        return {
            "success": True,
            "verification_url": verification_url,
            "channel_id": channel_id,
            "embed_code": f"[Verify Here]({verification_url})"
        }
        
    except Exception as e:
        logger.error(f"Bot send verification error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate verification: {str(e)}"
        )

# ==================== DASHBOARD ENDPOINTS ENHANCEMENT ====================

@app.get("/api/dashboard/servers")
async def get_user_servers_enhanced(user: Dict[str, Any] = Depends(verify_token)):
    """Enhanced server list for dashboard"""
    try:
        print(f"Getting servers for user: {user.get('username')}")
        
        # Get bot guilds
        bot_guilds = []
        if Config.DISCORD_BOT_TOKEN:
            headers = {"Authorization": f"Bot {Config.DISCORD_BOT_TOKEN}"}
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://discord.com/api/users/@me/guilds",
                    headers=headers
                ) as resp:
                    if resp.status == 200:
                        bot_guilds = await resp.json()
                        print(f"Bot is in {len(bot_guilds)} guilds")
                    else:
                        print(f"Failed to get bot guilds: {resp.status}")
        
        # Get user's access token
        access_token = user.get("access_token", "")
        user_guilds = []
        
        if access_token:
            try:
                async with aiohttp.ClientSession() as session:
                    headers = {"Authorization": f"Bearer {access_token}"}
                    async with session.get(
                        "https://discord.com/api/users/@me/guilds",
                        headers=headers
                    ) as resp:
                        if resp.status == 200:
                            user_guilds = await resp.json()
            except Exception as e:
                print(f"Error getting user guilds: {e}")
        
        # Create set of bot guild IDs for quick lookup
        bot_guild_ids = {guild["id"] for guild in bot_guilds}
        
        # Prepare response
        servers = []
        for guild in bot_guilds:
            guild_id = guild["id"]
            
            # Check if user has permission in this guild
            user_has_access = False
            user_permissions = 0
            
            for user_guild in user_guilds:
                if user_guild["id"] == guild_id:
                    user_has_access = True
                    user_permissions = int(user_guild.get("permissions", 0))
                    break
            
            # Skip if user doesn't have access
            if not user_has_access:
                continue
            
            # Check if user has admin permissions
            has_admin = (user_permissions & 0x8) != 0  # ADMINISTRATOR permission
            
            # Get verified count
            verified_count = db.get_guild_users_count(guild_id)
            
            # Get stats
            stats = db.get_guild_stats(guild_id)
            
            # Get server config
            config = db.get_server_config(guild_id)
            
            servers.append({
                "id": guild_id,
                "name": guild.get("name", "Unknown Server"),
                "icon": guild.get("icon"),
                "icon_url": f"https://cdn.discordapp.com/icons/{guild_id}/{guild['icon']}.png" if guild.get("icon") else None,
                "member_count": guild.get("approximate_member_count", 0),
                "verified_count": verified_count,
                "owner": guild.get("owner", False),
                "permissions": user_permissions,
                "has_admin": has_admin,
                "stats": stats,
                "config_configured": bool(config),
                "verification_channel": config.get("verification_channel") if config else None,
                "verification_role": config.get("verification_role") if config else None
            })
        
        print(f"Returning {len(servers)} servers to dashboard")
        return {
            "success": True,
            "servers": servers,
            "count": len(servers),
            "user_permissions": {
                "username": user.get("username"),
                "id": user.get("sub"),
                "can_manage_servers": len(servers) > 0
            }
        }
        
    except Exception as e:
        print(f"ERROR in get_user_servers: {e}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "servers": [],
            "count": 0,
            "error": str(e)
        }

# ==================== TRANSFER ENDPOINTS ENHANCEMENT ====================

@app.post("/api/dashboard/transfer/preview")
async def transfer_preview_enhanced(
    request: Request,
    user: Dict[str, Any] = Depends(verify_token)
):
    """Enhanced transfer preview"""
    try:
        data = await request.json()
        
        source_guild_id = data.get("source_guild_id")
        target_guild_id = data.get("target_guild_id")
        limit = data.get("limit", 100)
        status_filter = data.get("status", "verified")
        restored_filter = data.get("restored")
        
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
        users = db.get_users_for_transfer(
            source_guild_id,
            status=status_filter,
            restored=restored_filter,
            limit=limit
        )
        
        # Get server names
        source_guild_name = "Source Server"
        target_guild_name = "Target Server"
        
        if Config.DISCORD_BOT_TOKEN:
            try:
                async with aiohttp.ClientSession() as session:
                    headers = {"Authorization": f"Bot {Config.DISCORD_BOT_TOKEN}"}
                    
                    # Get source guild info
                    async with session.get(
                        f"https://discord.com/api/v10/guilds/{source_guild_id}",
                        headers=headers
                    ) as resp:
                        if resp.status == 200:
                            source_guild_info = await resp.json()
                            source_guild_name = source_guild_info.get("name", source_guild_name)
                    
                    # Get target guild info
                    async with session.get(
                        f"https://discord.com/api/v10/guilds/{target_guild_id}",
                        headers=headers
                    ) as resp:
                        if resp.status == 200:
                            target_guild_info = await resp.json()
                            target_guild_name = target_guild_info.get("name", target_guild_name)
            except:
                pass
        
        return {
            "success": True,
            "preview": {
                "source_guild": {
                    "id": source_guild_id,
                    "name": source_guild_name,
                    "user_count": len(users)
                },
                "target_guild": {
                    "id": target_guild_id,
                    "name": target_guild_name
                },
                "users": users[:10],  # First 10 users
                "total_users": len(users),
                "estimated_time": f"{(len(users) * 0.2):.1f} seconds"
            }
        }
        
    except Exception as e:
        logger.error(f"Transfer preview error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to preview transfer: {str(e)}"
        )

# ==================== HEALTH ENDPOINT ====================

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        # Check database
        supabase.table("verified_users").select("id").limit(1).execute()
        
        # Check Discord API
        discord_status = False
        if Config.DISCORD_BOT_TOKEN:
            try:
                async with aiohttp.ClientSession() as session:
                    headers = {"Authorization": f"Bot {Config.DISCORD_BOT_TOKEN}"}
                    async with session.get(
                        "https://discord.com/api/v10/gateway/bot",
                        headers=headers
                    ) as resp:
                        discord_status = resp.status == 200
            except:
                pass
        
        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "database": "connected",
            "discord_api": "reachable" if discord_status else "unreachable",
            "version": "5.0.0",
            "active_transfers": len(transfer_manager.active_transfers)
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service unhealthy"
        )

# ==================== VERIFICATION ENDPOINT ====================

@app.get("/api/verify/{guild_id}")
async def get_verification_url(guild_id: str):
    """Get verification URL for a guild"""
    try:
        state = secrets.token_urlsafe(32)
        
        # Save state
        db.save_oauth_state(
            state=state,
            guild_id=guild_id,
            type="verification"
        )
        
        # Generate verification URL
        redirect_uri = f"{Config.API_URL}/oauth/callback"
        auth_url = oauth.get_authorization_url(redirect_uri, state, guild_id)
        
        # QR code
        qr_code_url = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={urllib.parse.quote(auth_url)}"
        
        return {
            "success": True,
            "verification_url": auth_url,
            "qr_code_url": qr_code_url,
            "embed_code": f"[Verify Here]({auth_url})",
            "guild_id": guild_id,
            "expires_in": "10 minutes"
        }
        
    except Exception as e:
        logger.error(f"Error generating verification URL: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate verification URL"
        )

# User endpoints
@app.get("/api/dashboard/user")
async def get_dashboard_user(user: Dict[str, Any] = Depends(verify_token)):
    """Get current user data for dashboard"""
    try:
        # Get additional user info from Discord
        access_token = user.get("access_token", "")
        user_guilds = []
        
        if access_token:
            try:
                user_guilds = await oauth.get_user_guilds(access_token)
            except:
                pass  # Skip if we can't get guilds
        
        return {
            "success": True,
            "user": {
                "id": user.get("sub"),
                "username": user.get("username"),
                "avatar": user.get("avatar"),
                "email": user.get("email"),
                "access_token": access_token,
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
    """Get user's guilds where bot is present - SIMPLIFIED VERSION"""
    try:
        print(f"Getting servers for user: {user.get('sub')}")
        
        # Get bot guilds directly (no need to check user permissions for now)
        bot_guilds = []
        if Config.DISCORD_BOT_TOKEN:
            headers = {"Authorization": f"Bot {Config.DISCORD_BOT_TOKEN}"}
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://discord.com/api/users/@me/guilds",
                    headers=headers
                ) as resp:
                    if resp.status == 200:
                        bot_guilds = await resp.json()
                        print(f"Bot is in {len(bot_guilds)} guilds")
        
        servers = []
        for guild in bot_guilds:
            guild_id = guild["id"]
            
            # Get verified count
            try:
                verified_count = db.get_guild_users_count(guild_id)
            except:
                verified_count = 0
            
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
        
        print(f"Returning {len(servers)} servers to dashboard")
        return {
            "success": True,
            "servers": servers,
            "count": len(servers)
        }
        
    except Exception as e:
        print(f"ERROR in get_user_servers: {e}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "servers": [],
            "count": 0,
            "error": str(e)
        }

@app.get("/api/dashboard/servers/available")
async def get_available_servers(user: Dict[str, Any] = Depends(verify_token)):
    """Get list of servers the bot can transfer from"""
    try:
        # Get user's guilds
        access_token = user.get("access_token", "")
        user_guilds = []
        
        if access_token:
            try:
                async with aiohttp.ClientSession() as session:
                    headers = {"Authorization": f"Bearer {access_token}"}
                    async with session.get(
                        "https://discord.com/api/users/@me/guilds",
                        headers=headers
                    ) as resp:
                        if resp.status == 200:
                            user_guilds = await resp.json()
            except:
                pass
        
        # Get bot guilds
        bot_guilds = []
        if Config.DISCORD_BOT_TOKEN:
            try:
                bot_guilds = await oauth.get_bot_guilds()
            except:
                pass
        
        # Create set of bot guild IDs for fast lookup
        bot_guild_ids = {guild["id"] for guild in bot_guilds}
        
        available_guilds = []
        
        for guild in user_guilds:
            guild_id = guild["id"]
            guild_name = guild.get("name", "Unknown Server")
            
            # Check if bot is in guild and user has admin permissions
            if guild_id in bot_guild_ids and (int(guild.get("permissions", 0)) & 0x8):  # ADMIN permission
                # Get verified count
                verified_count = db.get_guild_users_count(guild_id)
                
                # Get stats
                stats = db.get_guild_stats(guild_id)
                
                available_guilds.append({
                    "id": guild_id,
                    "name": guild_name,
                    "icon": guild.get("icon"),
                    "icon_url": f"https://cdn.discordapp.com/icons/{guild_id}/{guild['icon']}.png" if guild.get("icon") else None,
                    "verified_count": verified_count,
                    "stats": stats,
                    "owner": guild.get("owner", False),
                    "permissions": guild.get("permissions", 0)
                })
        
        return {
            "success": True,
            "servers": available_guilds,
            "count": len(available_guilds)
        }
        
    except Exception as e:
        logger.error(f"Error getting available servers: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get available servers"
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
    status: str = "all",
    restored: bool = None,
    transferred: bool = None,
    limit: int = 50,
    offset: int = 0,
    user: Dict[str, Any] = Depends(verify_token)
):
    """Get server members with pagination"""
    try:
        members = db.get_users_by_guild(
            guild_id,
            status=status,
            restored=restored,
            transferred=transferred,
            limit=limit,
            offset=offset
        )
        
        total_count = db.get_guild_users_count(guild_id)
        
        return {
            "success": True,
            "members": members,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "total": total_count,
                "has_more": (offset + len(members)) < total_count
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
        state = secrets.token_urlsafe(32)
        
        # Save state with guild ID for verification
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
        
        # Use the user verification callback
        redirect_uri = f"{Config.API_URL}/oauth/callback"
        auth_url = oauth.get_authorization_url(redirect_uri, state, guild_id)
        
        # Generate QR code
        qr_code_url = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={urllib.parse.quote(auth_url)}"
        
        return {
            "success": True,
            "verification_url": auth_url,
            "qr_code_url": qr_code_url,
            "embed_code": f"[Verify Here]({auth_url})",
            "state": state,
            "expires_in": "10 minutes"
        }
        
    except Exception as e:
        logger.error(f"Error generating verification link: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate verification link"
        )

# Transfer endpoints
@app.get("/api/dashboard/transfer/servers")
async def get_transfer_servers(user: Dict[str, Any] = Depends(verify_token)):
    """Get servers available for transfer (both source and target)"""
    try:
        # Get user's guilds where bot is present and user has admin
        available_servers = await get_available_servers(user)
        
        if not available_servers.get("success"):
            raise HTTPException(status_code=500, detail="Failed to get servers")
        
        servers = available_servers.get("servers", [])
        
        return {
            "success": True,
            "servers": servers,
            "max_transfer_users": Config.MAX_TRANSFER_USERS
        }
        
    except Exception as e:
        logger.error(f"Error getting transfer servers: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get transfer servers"
        )

@app.post("/api/dashboard/transfer/preview")
async def preview_transfer(
    request: Request,
    user: Dict[str, Any] = Depends(verify_token)
):
    """Preview a transfer before executing"""
    try:
        data = await request.json()
        
        source_guild_id = data.get("source_guild_id")
        target_guild_id = data.get("target_guild_id")
        limit = data.get("limit", Config.MAX_TRANSFER_USERS)
        status_filter = data.get("status", "verified")
        restored_filter = data.get("restored")
        
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
        users = db.get_users_for_transfer(
            source_guild_id,
            status=status_filter,
            restored=restored_filter,
            limit=limit
        )
        
        # Get server info
        source_guild_info = None
        target_guild_info = None
        
        # Try to get guild names from Discord API
        if Config.DISCORD_BOT_TOKEN:
            try:
                async with aiohttp.ClientSession() as session:
                    headers = {"Authorization": f"Bot {Config.DISCORD_BOT_TOKEN}"}
                    
                    # Get source guild info
                    async with session.get(
                        f"https://discord.com/api/v10/guilds/{source_guild_id}",
                        headers=headers
                    ) as resp:
                        if resp.status == 200:
                            source_guild_info = await resp.json()
                    
                    # Get target guild info
                    async with session.get(
                        f"https://discord.com/api/v10/guilds/{target_guild_id}",
                        headers=headers
                    ) as resp:
                        if resp.status == 200:
                            target_guild_info = await resp.json()
            except:
                pass
        
        return {
            "success": True,
            "preview": {
                "source_guild": {
                    "id": source_guild_id,
                    "name": source_guild_info.get("name") if source_guild_info else "Unknown Server",
                    "user_count": len(users)
                },
                "target_guild": {
                    "id": target_guild_id,
                    "name": target_guild_info.get("name") if target_guild_info else "Unknown Server"
                },
                "users": users[:10],  # Return first 10 users for preview
                "total_users": len(users),
                "estimated_time": f"{len(users) * 0.1:.1f} seconds"
            }
        }
        
    except Exception as e:
        logger.error(f"Error previewing transfer: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to preview transfer: {str(e)}"
        )

@app.post("/api/dashboard/transfer/execute")
async def execute_transfer(
    background_tasks: BackgroundTasks,
    request: Request,
    user: Dict[str, Any] = Depends(verify_token)
):
    """Execute a user transfer between servers"""
    try:
        data = await request.json()
        
        source_guild_id = data.get("source_guild_id")
        target_guild_id = data.get("target_guild_id")
        user_ids = data.get("user_ids", [])
        limit = data.get("limit")
        assign_role_id = data.get("assign_role_id")
        remove_from_source = data.get("remove_from_source", False)
        transfer_all = data.get("transfer_all", False)
        
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
        
        # Get user IDs to transfer
        if transfer_all:
            # Get all users from source guild
            users = db.get_users_for_transfer(
                source_guild_id,
                limit=limit or Config.MAX_TRANSFER_USERS
            )
            user_ids = [user["discord_id"] for user in users]
        elif not user_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No users specified for transfer"
            )
        
        # Apply limit if specified
        if limit and len(user_ids) > limit:
            user_ids = user_ids[:limit]
        
        # Check if user has permission in both guilds
        # (This would require checking Discord API for user's permissions in each guild)
        
        # Create transfer job
        job_id = str(uuid.uuid4())
        
        job_data = TransferJob(
            job_id=job_id,
            source_guild_id=source_guild_id,
            target_guild_id=target_guild_id,
            user_id=user.get("sub"),
            config={
                "assign_role_id": assign_role_id,
                "remove_from_source": remove_from_source,
                "limit": limit,
                "total_users": len(user_ids)
            }
        )
        
        db.create_transfer_job(job_data)
        
        # Add log entry
        db.add_log(
            log_type="transfer",
            message=f"Started transfer job {job_id}: {len(user_ids)} users from {source_guild_id} to {target_guild_id}",
            guild_id=target_guild_id,
            user_id=user.get("sub"),
            metadata={
                "job_id": job_id,
                "source_guild": source_guild_id,
                "target_guild": target_guild_id,
                "user_count": len(user_ids),
                "assign_role_id": assign_role_id
            }
        )
        
        # Start background transfer task
        background_tasks.add_task(
            transfer_manager.process_transfer,
            job_id,
            source_guild_id,
            target_guild_id,
            user_ids,
            user,
            job_data.config
        )
        
        return {
            "success": True,
            "job_id": job_id,
            "message": f"Transfer started for {len(user_ids)} users",
            "status": "pending",
            "estimated_time": f"{len(user_ids) * 0.1:.1f} seconds"
        }
        
    except Exception as e:
        logger.error(f"Error executing transfer: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to execute transfer: {str(e)}"
        )

@app.get("/api/dashboard/transfer/jobs")
async def get_transfer_jobs(
    guild_id: Optional[str] = None,
    limit: int = 10,
    user: Dict[str, Any] = Depends(verify_token)
):
    """Get transfer jobs for the current user"""
    try:
        jobs = db.get_user_transfer_jobs(
            user_id=user.get("sub"),
            guild_id=guild_id,
            limit=limit
        )
        
        return {
            "success": True,
            "jobs": jobs,
            "count": len(jobs)
        }
        
    except Exception as e:
        logger.error(f"Error getting transfer jobs: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get transfer jobs"
        )

@app.get("/api/dashboard/transfer/job/{job_id}")
async def get_transfer_job_status(
    job_id: str,
    user: Dict[str, Any] = Depends(verify_token)
):
    """Get status of a specific transfer job"""
    try:
        job = db.get_transfer_job(job_id)
        
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Transfer job not found"
            )
        
        # Check if user owns this job
        if job.get("user_id") != user.get("sub"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to view this job"
            )
        
        return {
            "success": True,
            "job": job
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting transfer job status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get transfer job status"
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
        discord_status = "unknown"
        discord_data = {}
        
        if Config.DISCORD_BOT_TOKEN:
            try:
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
            except:
                discord_status = "error"
        
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
            "version": "5.0.0",
            "discord": discord_status,
            "database": "connected",
            "stats": {
                "total_users": total_users_count,
                "shards": discord_data.get("shards", 1),
                "session_start_limit": discord_data.get("session_start_limit", {})
            },
            "features": {
                "verification": True,
                "transfers": True,
                "dashboard": True
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting bot status: {e}")
        return {
            "status": "error",
            "timestamp": datetime.now().isoformat(),
            "version": "5.0.0",
            "error": str(e)
        }

@app.get("/api/bot/guild/{guild_id}/verified")
async def get_guild_verified_users(guild_id: str):
    """Get verified users for a guild (for bot restoration)"""
    try:
        users = db.get_users_by_guild(guild_id, status="verified", restored=False)
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

@app.post("/api/bot/transfer-users")
async def bot_transfer_users(request: Request):
    """Bot endpoint to transfer users between servers"""
    try:
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
        
        transferred = []
        failed = []
        
        for user_id in user_ids:
            success = db.transfer_user(
                discord_id=user_id,
                source_guild_id=source_guild_id,
                target_guild_id=target_guild_id,
                metadata={"bot_transfer": True, "role_id": role_id},
                remove_from_source=remove_from_source
            )
            
            if success:
                transferred.append(user_id)
                
                # Mark as restored in target guild
                db.mark_user_restored(user_id, target_guild_id, role_id)
            else:
                failed.append(user_id)
        
        # Add log entry
        db.add_log(
            log_type="transfer",
            message=f"Bot transferred {len(transferred)} users from {source_guild_id} to {target_guild_id}",
            guild_id=target_guild_id,
            metadata={
                "source_guild": source_guild_id,
                "target_guild": target_guild_id,
                "transferred": transferred,
                "failed": failed,
                "role_id": role_id,
                "remove_from_source": remove_from_source,
                "source": "bot_api"
            }
        )
        
        return {
            "success": True,
            "transferred": len(transferred),
            "failed": len(failed),
            "transferred_ids": transferred,
            "failed_ids": failed
        }
        
    except Exception as e:
        logger.error(f"Bot transfer error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Transfer failed: {str(e)}"
        )

# ==================== UTILITY ENDPOINTS ====================

@app.get("/api/verify/{guild_id}")
async def get_verification_url_public(guild_id: str):
    """Get verification URL for a specific guild (public)"""
    try:
        state = secrets.token_urlsafe(32)
        
        # Save state with guild ID for verification
        db.save_oauth_state(
            state=state,
            guild_id=guild_id,
            type="public_verification"
        )
        
        # Use the user verification callback
        redirect_uri = f"{Config.API_URL}/oauth/callback"
        auth_url = oauth.get_authorization_url(redirect_uri, state, guild_id)
        
        return {
            "success": True,
            "verification_url": auth_url,
            "embed_code": f"[Verify Here]({auth_url})",
            "qr_code": f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={urllib.parse.quote(auth_url)}",
            "guild_id": guild_id
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
        "api:app",
        host="0.0.0.0",
        port=port,
        reload=os.getenv("ENVIRONMENT") == "development",
        log_level="info",
        access_log=True
    )


