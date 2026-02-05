from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import uvicorn
import os
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import json
import aiohttp
from supabase import create_client, Client
from cryptography.fernet import Fernet
import base64
import urllib.parse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="xotiicsverify API",
    description="Secure Discord verification system backend",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security
security = HTTPBearer()

# Initialize Supabase
supabase_url = os.environ.get('SUPABASE_URL')
supabase_key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
supabase: Client = create_client(supabase_url, supabase_key)

# Rate limiting storage
rate_limit_cache = {}

class DatabaseManager:
    def __init__(self):
        self.cipher_key = os.environ.get('ENCRYPTION_KEY')
        if not self.cipher_key:
            self.cipher_key = Fernet.generate_key()
        self.cipher = Fernet(base64.urlsafe_b64encode(self.cipher_key.encode()[:32]))
    
    def _encrypt(self, data: str) -> str:
        return self.cipher.encrypt(data.encode()).decode()
    
    def _decrypt(self, encrypted_data: str) -> str:
        return self.cipher.decrypt(encrypted_data.encode()).decode()
    
    def save_oauth_state(self, state: str, user_id: str = None, guild_id: str = None, redirect_url: str = None):
        try:
            data = {
                'state': state,
                'user_id': user_id,
                'guild_id': guild_id,
                'redirect_url': redirect_url
            }
            supabase.table('oauth_states').insert(data).execute()
        except Exception as e:
            logger.error(f"Error saving OAuth state: {e}")
    
    def get_oauth_state(self, state: str) -> Optional[Dict[str, Any]]:
        try:
            response = supabase.table('oauth_states')\
                .select('*')\
                .eq('state', state)\
                .execute()
            
            if response.data:
                state_data = response.data[0]
                # Delete after retrieval
                supabase.table('oauth_states').delete().eq('state', state).execute()
                
                return {
                    'state': state_data['state'],
                    'user_id': state_data['user_id'],
                    'guild_id': state_data['guild_id'],
                    'redirect_url': state_data['redirect_url']
                }
        except Exception as e:
            logger.error(f"Error getting OAuth state: {e}")
        return None
    
    def add_verified_user(self, discord_id: str, username: str, access_token: str, 
                         refresh_token: str, expires_in: int, guild_id: str, metadata: dict = None) -> bool:
        try:
            expires_at = datetime.now() + timedelta(seconds=expires_in)
            
            data = {
                'discord_id': discord_id,
                'username': username,
                'access_token': self._encrypt(access_token),
                'refresh_token': self._encrypt(refresh_token),
                'expires_at': expires_at.isoformat(),
                'guild_id': guild_id,
                'metadata': metadata or {}
            }
            
            supabase.table('verified_users').upsert(data, on_conflict='discord_id').execute()
            logger.info(f"Added/updated verified user: {username} ({discord_id})")
            return True
        except Exception as e:
            logger.error(f"Error adding verified user: {e}")
            return False
    
    def get_user_servers(self, user_id: str) -> List[Dict[str, Any]]:
        try:
            # Get servers owned by user
            response = supabase.table('server_settings')\
                .select('guild_id, settings')\
                .eq('owner_id', user_id)\
                .execute()
            
            servers = []
            for server in response.data:
                # Get verified count for each server
                count_response = supabase.table('verified_users')\
                    .select('id', count='exact')\
                    .eq('guild_id', server['guild_id'])\
                    .execute()
                
                servers.append({
                    'guild_id': server['guild_id'],
                    'settings': server.get('settings', {}),
                    'verified_count': count_response.count or 0
                })
            return servers
        except Exception as e:
            logger.error(f"Error getting user servers: {e}")
            return []
    
    def update_server_settings(self, guild_id: str, owner_id: str, settings: dict):
        try:
            data = {
                'guild_id': guild_id,
                'owner_id': owner_id,
                'settings': settings
            }
            supabase.table('server_settings').upsert(data, on_conflict='guild_id').execute()
            return True
        except Exception as e:
            logger.error(f"Error updating server settings: {e}")
            return False

class OAuthHandler:
    def __init__(self):
        self.client_id = os.environ.get('DISCORD_CLIENT_ID')
        self.client_secret = os.environ.get('DISCORD_CLIENT_SECRET')
        self.redirect_uri = os.environ.get('REDIRECT_URI')
        self.bot_token = os.environ.get('DISCORD_BOT_TOKEN')
    
    async def exchange_code(self, code: str) -> Optional[Dict[str, Any]]:
        try:
            data = {
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': self.redirect_uri,
                'scope': 'identify guilds.join'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post('https://discord.com/api/oauth2/token', data=data) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        error_text = await resp.text()
                        logger.error(f"Token exchange failed: {resp.status} - {error_text}")
        except Exception as e:
            logger.error(f"Error exchanging code: {e}")
        return None
    
    async def get_user_info(self, access_token: str) -> Optional[Dict[str, Any]]:
        try:
            headers = {'Authorization': f'Bearer {access_token}'}
            async with aiohttp.ClientSession() as session:
                async with session.get('https://discord.com/api/users/@me', headers=headers) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        logger.error(f"Failed to get user info: {resp.status}")
        except Exception as e:
            logger.error(f"Error getting user info: {e}")
        return None
    
    async def get_user_guilds(self, access_token: str) -> Optional[list]:
        try:
            headers = {'Authorization': f'Bearer {access_token}'}
            async with aiohttp.ClientSession() as session:
                async with session.get('https://discord.com/api/users/@me/guilds', headers=headers) as resp:
                    if resp.status == 200:
                        guilds = await resp.json()
                        # Filter to guilds where user has admin permissions
                        return [g for g in guilds if (g.get('permissions') & 0x8) == 0x8]
                    else:
                        logger.error(f"Failed to get user guilds: {resp.status}")
        except Exception as e:
            logger.error(f"Error getting user guilds: {e}")
        return None

db = DatabaseManager()
oauth = OAuthHandler()

def rate_limit(request: Request, limit: int = 10, window: int = 60) -> bool:
    """Simple rate limiting"""
    client_ip = request.client.host
    key = f"{client_ip}_{request.url.path}"
    
    current_time = datetime.now().timestamp()
    window_start = current_time - window
    
    # Clean old entries
    rate_limit_cache[key] = [
        timestamp for timestamp in rate_limit_cache.get(key, [])
        if timestamp > window_start
    ]
    
    if len(rate_limit_cache[key]) >= limit:
        return False
    
    rate_limit_cache[key].append(current_time)
    return True

def verify_auth(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Dict[str, Any]:
    """Verify API token"""
    token = credentials.credentials
    api_key = os.environ.get('API_SECRET_KEY')
    
    if token != api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token"
        )
    
    return {"authenticated": True}

@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "xotiicsverify API",
        "version": "1.0.0",
        "docs": "/docs"
    }

@app.get("/oauth/callback")
async def oauth_callback(code: str, state: str):
    """OAuth2 callback endpoint"""
    try:
        if not rate_limit:
            raise HTTPException(status_code=429, detail="Too many requests")
        
        # Verify state
        state_data = db.get_oauth_state(state)
        if not state_data:
            raise HTTPException(status_code=400, detail="Invalid or expired state")
        
        # Exchange code for tokens
        token_data = await oauth.exchange_code(code)
        if not token_data:
            raise HTTPException(status_code=400, detail="Failed to exchange code")
        
        # Get user info
        user_info = await oauth.get_user_info(token_data['access_token'])
        if not user_info:
            raise HTTPException(status_code=400, detail="Failed to get user info")
        
        discord_id = user_info['id']
        username = f"{user_info['username']}#{user_info['discriminator']}"
        
        # Store verified user
        success = db.add_verified_user(
            discord_id=discord_id,
            username=username,
            access_token=token_data['access_token'],
            refresh_token=token_data['refresh_token'],
            expires_in=token_data['expires_in'],
            guild_id=state_data.get('guild_id', ''),
            metadata={
                'avatar': user_info.get('avatar'),
                'verified': user_info.get('verified', False)
            }
        )
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to store user data")
        
        # Redirect based on state
        redirect_url = state_data.get('redirect_url') or os.environ.get('FRONTEND_URL', '')
        if redirect_url:
            return RedirectResponse(f"{redirect_url}/success?user_id={discord_id}")
        
        return {
            "success": True,
            "message": "Verification successful!",
            "user_id": discord_id,
            "username": username
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/auth/discord")
async def auth_discord(redirect_url: Optional[str] = None):
    """Initiate Discord OAuth for web dashboard"""
    try:
        state = secrets.token_urlsafe(32)
        db.save_oauth_state(
            state=state,
            redirect_url=redirect_url
        )
        
        params = {
            'client_id': oauth.client_id,
            'redirect_uri': f"{os.environ.get('API_URL')}/api/auth/callback",
            'response_type': 'code',
            'scope': 'identify guilds',
            'state': state
        }
        
        auth_url = f"https://discord.com/api/oauth2/authorize?{urllib.parse.urlencode(params)}"
        return RedirectResponse(auth_url)
        
    except Exception as e:
        logger.error(f"Auth discord error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/auth/callback")
async def auth_callback(code: str, state: str):
    """Callback for web dashboard authentication"""
    try:
        # Verify state
        state_data = db.get_oauth_state(state)
        if not state_data:
            raise HTTPException(status_code=400, detail="Invalid or expired state")
        
        # Exchange code for tokens
        token_data = await oauth.exchange_code(code)
        if not token_data:
            raise HTTPException(status_code=400, detail="Failed to exchange code")
        
        # Get user info
        user_info = await oauth.get_user_info(token_data['access_token'])
        if not user_info:
            raise HTTPException(status_code=400, detail="Failed to get user info")
        
        # Get user's guilds
        guilds = await oauth.get_user_guilds(token_data['access_token'])
        
        # Create session token
        session_token = secrets.token_urlsafe(32)
        
        # Store user session (in production, use Redis)
        user_data = {
            'id': user_info['id'],
            'username': user_info['username'],
            'discriminator': user_info['discriminator'],
            'avatar': user_info.get('avatar'),
            'guilds': guilds or [],
            'access_token': token_data['access_token'],
            'refresh_token': token_data['refresh_token'],
            'expires_at': (datetime.now() + timedelta(seconds=token_data['expires_in'])).isoformat()
        }
        
        # Redirect to frontend
        redirect_url = state_data.get('redirect_url') or os.environ.get('FRONTEND_URL', '')
        if redirect_url:
            encoded_data = urllib.parse.quote(json.dumps({
                'success': True,
                'user': user_data,
                'session_token': session_token
            }))
            return RedirectResponse(f"{redirect_url}/dashboard?auth_data={encoded_data}")
        
        return {
            "success": True,
            "user": user_data,
            "session_token": session_token
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Auth callback error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/servers")
async def get_servers(user_id: str, auth: dict = Depends(verify_auth)):
    """Get servers owned by the authenticated user"""
    try:
        servers = db.get_user_servers(user_id)
        
        return {
            "success": True,
            "servers": servers
        }
        
    except Exception as e:
        logger.error(f"Get servers error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/restore")
async def trigger_restore(guild_id: str, auth: dict = Depends(verify_auth)):
    """Trigger restoration of verified users"""
    try:
        # In production, this would trigger an async task or webhook
        # For now, return success
        
        return {
            "success": True,
            "message": "Restoration process started",
            "guild_id": guild_id,
            "task_id": secrets.token_urlsafe(16),
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Trigger restore error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.put("/api/settings/{guild_id}")
async def update_settings(
    guild_id: str,
    settings: Dict[str, Any],
    auth: dict = Depends(verify_auth)
):
    """Update server settings"""
    try:
        # Get user_id from settings or request
        user_id = settings.get('owner_id')
        if not user_id:
            raise HTTPException(status_code=400, detail="Owner ID required")
        
        success = db.update_server_settings(
            guild_id=guild_id,
            owner_id=user_id,
            settings=settings
        )
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update settings")
        
        return {
            "success": True,
            "message": "Settings updated successfully",
            "guild_id": guild_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update settings error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/stats/{guild_id}")
async def get_stats(guild_id: str, auth: dict = Depends(verify_auth)):
    """Get server statistics"""
    try:
        # Get verified count
        response = supabase.table('verified_users')\
            .select('id', count='exact')\
            .eq('guild_id', guild_id)\
            .execute()
        
        total_verified = response.count or 0
        
        # Get restored count
        restored_response = supabase.table('verified_users')\
            .select('id', count='exact')\
            .eq('guild_id', guild_id)\
            .not_.is_('restored_at', 'null')\
            .execute()
        
        restored_count = restored_response.count or 0
        
        # Get daily stats
        daily_response = supabase.table('verified_users')\
            .select('verified_at')\
            .eq('guild_id', guild_id)\
            .order('verified_at', desc=True)\
            .limit(100)\
            .execute()
        
        daily_stats = {}
        for user in daily_response.data:
            if user['verified_at']:
                date = datetime.fromisoformat(user['verified_at'].replace('Z', '+00:00')).strftime('%Y-%m-%d')
                daily_stats[date] = daily_stats.get(date, 0) + 1
        
        return {
            "success": True,
            "stats": {
                "total_verified": total_verified,
                "restored": restored_count,
                "pending": total_verified - restored_count,
                "daily_stats": daily_stats
            }
        }
        
    except Exception as e:
        logger.error(f"Get stats error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        # Test database connection
        supabase.table('verified_users').select('id').limit(1).execute()
        
        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "service": "xotiicsverify API",
            "database": "connected"
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=500, detail="Database connection failed")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=False)