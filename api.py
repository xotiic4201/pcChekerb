from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import uvicorn
import os
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import json
import aiohttp
from supabase import create_client, Client
from cryptography.fernet import Fernet
import base64
import urllib.parse
import traceback

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="xotiicsverify API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()
supabase: Optional[Client] = None

class DatabaseManager:
    def __init__(self):
        key = os.environ.get('ENCRYPTION_KEY', Fernet.generate_key())
        if len(key) < 32:
            key = key.ljust(32, '0')[:32]
        self.cipher = Fernet(base64.urlsafe_b64encode(key.encode()[:32]))
    
    def _encrypt(self, data: str) -> str:
        return self.cipher.encrypt(data.encode()).decode()
    
    def _decrypt(self, encrypted_data: str) -> str:
        return self.cipher.decrypt(encrypted_data.encode()).decode()
    
    def save_oauth_state(self, state: str, user_id: str = None, guild_id: str = None, redirect_url: str = None) -> bool:
        try:
            if not supabase:
                return False
            data = {'state': state, 'user_id': user_id, 'guild_id': guild_id, 'redirect_url': redirect_url}
            supabase.table('oauth_states').insert(data).execute()
            return True
        except Exception:
            return False
    
    def get_oauth_state(self, state: str) -> Optional[Dict[str, Any]]:
        try:
            if not supabase:
                return None
            response = supabase.table('oauth_states').select('*').eq('state', state).execute()
            if response.data:
                state_data = response.data[0]
                supabase.table('oauth_states').delete().eq('state', state).execute()
                return state_data
        except Exception:
            return None
        return None
    
    def add_verified_user(self, discord_id: str, username: str, access_token: str, 
                         refresh_token: str, expires_in: int, guild_id: str, metadata: dict = None) -> bool:
        try:
            if not supabase:
                return False
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
            return True
        except Exception:
            return False
    
    def get_user_servers(self, user_id: str) -> List[Dict[str, Any]]:
        try:
            if not supabase:
                return []
            response = supabase.table('server_settings').select('guild_id, settings').eq('owner_id', user_id).execute()
            servers = []
            for server in response.data:
                count_response = supabase.table('verified_users').select('id', count='exact').eq('guild_id', server['guild_id']).execute()
                servers.append({
                    'guild_id': server['guild_id'],
                    'settings': server.get('settings', {}),
                    'verified_count': count_response.count or 0
                })
            return servers
        except Exception:
            return []
    
    def update_server_settings(self, guild_id: str, owner_id: str, settings: dict) -> bool:
        try:
            if not supabase:
                return False
            data = {'guild_id': guild_id, 'owner_id': owner_id, 'settings': settings}
            supabase.table('server_settings').upsert(data, on_conflict='guild_id').execute()
            return True
        except Exception:
            return False

db = DatabaseManager()

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
                        logger.error(f"Token exchange failed: {resp.status}")
                        return None
        except Exception:
            return None
    
    async def get_user_info(self, access_token: str) -> Optional[Dict[str, Any]]:
        try:
            headers = {'Authorization': f'Bearer {access_token}'}
            async with aiohttp.ClientSession() as session:
                async with session.get('https://discord.com/api/users/@me', headers=headers) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    return None
        except Exception:
            return None
    
    async def get_user_guilds(self, access_token: str) -> Optional[list]:
        try:
            headers = {'Authorization': f'Bearer {access_token}'}
            async with aiohttp.ClientSession() as session:
                async with session.get('https://discord.com/api/users/@me/guilds', headers=headers) as resp:
                    if resp.status == 200:
                        guilds = await resp.json()
                        return [g for g in guilds if (g.get('permissions') & 0x8) == 0x8]
                    return None
        except Exception:
            return None

oauth = OAuthHandler()

rate_limit_cache = {}

def rate_limit(request: Request, limit: int = 10, window: int = 60) -> bool:
    client_ip = request.client.host if request.client else 'unknown'
    key = f"{client_ip}_{request.url.path}"
    current_time = datetime.now().timestamp()
    window_start = current_time - window
    rate_limit_cache[key] = [t for t in rate_limit_cache.get(key, []) if t > window_start]
    if len(rate_limit_cache[key]) >= limit:
        return False
    rate_limit_cache[key].append(current_time)
    return True

def verify_auth(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Dict[str, Any]:
    token = credentials.credentials
    api_key = os.environ.get('API_SECRET_KEY')
    if token != api_key:
        raise HTTPException(status_code=401, detail="Invalid token")
    return {"authenticated": True}

@app.on_event("startup")
async def startup_event():
    global supabase
    try:
        supabase_url = os.environ.get('SUPABASE_URL')
        supabase_key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
        if supabase_url and supabase_key:
            supabase = create_client(supabase_url, supabase_key)
            logger.info("Supabase connected")
    except Exception as e:
        logger.error(f"Supabase connection failed: {e}")

@app.get("/")
async def root():
    return {"status": "online", "service": "xotiicsverify API"}

@app.get("/oauth/callback")
async def oauth_callback(code: str, state: str):
    try:
        if not rate_limit:
            raise HTTPException(status_code=429, detail="Too many requests")
        
        state_data = db.get_oauth_state(state)
        if not state_data:
            raise HTTPException(status_code=400, detail="Invalid state")
        
        token_data = await oauth.exchange_code(code)
        if not token_data:
            raise HTTPException(status_code=400, detail="Failed to exchange code")
        
        user_info = await oauth.get_user_info(token_data['access_token'])
        if not user_info:
            raise HTTPException(status_code=400, detail="Failed to get user info")
        
        discord_id = user_info['id']
        username = f"{user_info['username']}#{user_info['discriminator']}"
        
        success = db.add_verified_user(
            discord_id=discord_id,
            username=username,
            access_token=token_data['access_token'],
            refresh_token=token_data['refresh_token'],
            expires_in=token_data['expires_in'],
            guild_id=state_data.get('guild_id', ''),
            metadata={'avatar': user_info.get('avatar')}
        )
        
        if not success:
            raise HTTPException(status_code=500, detail="Database error")
        
        redirect_url = state_data.get('redirect_url') or os.environ.get('FRONTEND_URL', '')
        if redirect_url:
            return RedirectResponse(f"{redirect_url}/?success=true&user_id={discord_id}")
        
        return {"success": True, "user_id": discord_id, "username": username}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"OAuth error: {e}")
        raise HTTPException(status_code=500, detail="Internal error")

@app.get("/api/auth/discord")
async def auth_discord(redirect_url: Optional[str] = None):
    try:
        state = secrets.token_urlsafe(32)
        db.save_oauth_state(state=state, redirect_url=redirect_url)
        
        params = {
            'client_id': oauth.client_id,
            'redirect_uri': f"{os.environ.get('API_URL', '')}/api/auth/callback",
            'response_type': 'code',
            'scope': 'identify guilds',
            'state': state
        }
        auth_url = f"https://discord.com/api/oauth2/authorize?{urllib.parse.urlencode(params)}"
        return RedirectResponse(auth_url)
    except Exception:
        raise HTTPException(status_code=500, detail="Auth failed")

@app.get("/api/auth/callback")
async def auth_callback(code: str, state: str):
    try:
        state_data = db.get_oauth_state(state)
        if not state_data:
            raise HTTPException(status_code=400, detail="Invalid state")
        
        token_data = await oauth.exchange_code(code)
        if not token_data:
            raise HTTPException(status_code=400, detail="Failed to exchange code")
        
        user_info = await oauth.get_user_info(token_data['access_token'])
        if not user_info:
            raise HTTPException(status_code=400, detail="Failed to get user info")
        
        guilds = await oauth.get_user_guilds(token_data['access_token'])
        
        user_data = {
            'id': user_info['id'],
            'username': user_info['username'],
            'discriminator': user_info['discriminator'],
            'avatar': user_info.get('avatar'),
            'guilds': guilds or []
        }
        
        redirect_url = state_data.get('redirect_url') or os.environ.get('FRONTEND_URL', '')
        if redirect_url:
            encoded_data = urllib.parse.quote(json.dumps({'success': True, 'user': user_data}))
            return RedirectResponse(f"{redirect_url}/?auth_data={encoded_data}")
        
        return {"success": True, "user": user_data}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal error")

@app.get("/api/servers")
async def get_servers(user_id: str, auth: dict = Depends(verify_auth)):
    servers = db.get_user_servers(user_id)
    return {"success": True, "servers": servers}

@app.post("/api/restore")
async def trigger_restore(guild_id: str, auth: dict = Depends(verify_auth)):
    return {
        "success": True,
        "message": "Restoration started",
        "guild_id": guild_id,
        "task_id": secrets.token_urlsafe(16)
    }

@app.put("/api/settings/{guild_id}")
async def update_settings(guild_id: str, settings: Dict[str, Any], auth: dict = Depends(verify_auth)):
    user_id = settings.get('owner_id')
    if not user_id:
        raise HTTPException(status_code=400, detail="Owner ID required")
    
    success = db.update_server_settings(guild_id, user_id, settings)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update")
    
    return {"success": True, "message": "Settings updated"}

@app.get("/api/stats/{guild_id}")
async def get_stats(guild_id: str, auth: dict = Depends(verify_auth)):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not connected")
    
    total_response = supabase.table('verified_users').select('id', count='exact').eq('guild_id', guild_id).execute()
    restored_response = supabase.table('verified_users').select('id', count='exact').eq('guild_id', guild_id).not_.is_('restored_at', 'null').execute()
    
    total = total_response.count or 0
    restored = restored_response.count or 0
    
    return {
        "success": True,
        "stats": {
            "total_verified": total,
            "restored": restored,
            "pending": total - restored
        }
    }

@app.get("/health")
async def health_check():
    db_status = "connected" if supabase else "disconnected"
    return {"status": "healthy", "database": db_status, "timestamp": datetime.now().isoformat()}

@app.get("/debug")
async def debug_info():
    return {
        "client_id_set": bool(os.environ.get('DISCORD_CLIENT_ID')),
        "client_secret_set": bool(os.environ.get('DISCORD_CLIENT_SECRET')),
        "redirect_uri": os.environ.get('REDIRECT_URI'),
        "supabase_connected": bool(supabase)
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=False)
