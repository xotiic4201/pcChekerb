from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
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
import urllib.parse
from supabase import create_client, Client
from cryptography.fernet import Fernet
import base64
import jwt
from pydantic import BaseModel

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="xotiicsverify API",
    description="Secure Discord verification system backend with dashboard",
    version="3.0.0"
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security
security = HTTPBearer()
JWT_SECRET = os.environ.get('JWT_SECRET', secrets.token_urlsafe(32))

# Initialize Supabase
supabase_url = os.environ.get('SUPABASE_URL')
supabase_key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')

if not supabase_url or not supabase_key:
    logger.error("Missing Supabase credentials!")
    raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")

supabase: Client = create_client(supabase_url, supabase_key)

# Pydantic models
class BotConfig(BaseModel):
    default_role: Optional[str] = None
    auto_assign_role: bool = True
    send_welcome_dm: bool = False
    min_account_age: int = 7
    verification_timeout: int = 15
    require_email: bool = True
    enable_captcha: bool = False

class ServerConfig(BaseModel):
    verification_channel: Optional[str] = None
    verification_role: Optional[str] = None
    welcome_message: Optional[str] = None
    enable_auto_verification: bool = True

# Database Manager
class DatabaseManager:
    def __init__(self):
        self.cipher_key = os.environ.get('ENCRYPTION_KEY')
        if not self.cipher_key:
            self.cipher_key = base64.urlsafe_b64encode(Fernet.generate_key()).decode()
            logger.warning("Generated new encryption key")
        
        if isinstance(self.cipher_key, str):
            self.cipher_key = self.cipher_key.encode()
        
        key = base64.urlsafe_b64encode(self.cipher_key[:32].ljust(32, b'0'))
        self.cipher = Fernet(key)
    
    def _encrypt(self, data: str) -> str:
        try:
            return self.cipher.encrypt(data.encode()).decode()
        except Exception as e:
            logger.error(f"Encryption error: {e}")
            raise
    
    def _decrypt(self, encrypted_data: str) -> str:
        try:
            return self.cipher.decrypt(encrypted_data.encode()).decode()
        except Exception as e:
            logger.error(f"Decryption error: {e}")
            raise
    
    # OAuth State Management
    def save_oauth_state(self, state: str, user_id: str = None, guild_id: str = None, redirect_url: str = None):
        try:
            data = {
                'state': state,
                'user_id': user_id,
                'guild_id': guild_id,
                'redirect_url': redirect_url,
                'created_at': datetime.now().isoformat()
            }
            supabase.table('oauth_states').insert(data).execute()
            logger.info(f"Saved OAuth state for guild {guild_id}")
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
                supabase.table('oauth_states').delete().eq('state', state).execute()
                
                return {
                    'state': state_data['state'],
                    'user_id': state_data.get('user_id'),
                    'guild_id': state_data.get('guild_id'),
                    'redirect_url': state_data.get('redirect_url')
                }
        except Exception as e:
            logger.error(f"Error getting OAuth state: {e}")
        return None
    
    # User Management
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
                'metadata': metadata or {},
                'verified_at': datetime.now().isoformat(),
                'restored': False,
                'status': 'verified'
            }
            
            supabase.table('verified_users').upsert(data, on_conflict='discord_id,guild_id').execute()
            logger.info(f"Added verified user: {username} for guild {guild_id}")
            return True
        except Exception as e:
            logger.error(f"Error adding verified user: {e}")
            return False
    
    def get_user(self, discord_id: str, guild_id: str) -> Optional[Dict[str, Any]]:
        try:
            response = supabase.table('verified_users')\
                .select('*')\
                .eq('discord_id', discord_id)\
                .eq('guild_id', guild_id)\
                .execute()
            
            if response.data:
                user = response.data[0]
                return {
                    'discord_id': user['discord_id'],
                    'username': user['username'],
                    'access_token': self._decrypt(user['access_token']),
                    'refresh_token': self._decrypt(user['refresh_token']),
                    'expires_at': datetime.fromisoformat(user['expires_at'].replace('Z', '+00:00')),
                    'guild_id': user['guild_id'],
                    'verified_at': datetime.fromisoformat(user['verified_at'].replace('Z', '+00:00')) if user.get('verified_at') else None,
                    'restored': user.get('restored', False),
                    'restored_at': datetime.fromisoformat(user['restored_at'].replace('Z', '+00:00')) if user.get('restored_at') else None,
                    'status': user.get('status', 'pending'),
                    'metadata': user.get('metadata', {})
                }
        except Exception as e:
            logger.error(f"Error getting user: {e}")
        return None
    
    def get_guild_users(self, guild_id: str, status: str = None, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        try:
            query = supabase.table('verified_users')\
                .select('*')\
                .eq('guild_id', guild_id)\
                .order('verified_at', desc=True)\
                .range(offset, offset + limit - 1)
            
            if status:
                query = query.eq('status', status)
            
            response = query.execute()
            
            users = []
            for user in response.data:
                try:
                    users.append({
                        'discord_id': user['discord_id'],
                        'username': user['username'],
                        'guild_id': user['guild_id'],
                        'verified_at': user.get('verified_at'),
                        'restored': user.get('restored', False),
                        'restored_at': user.get('restored_at'),
                        'status': user.get('status', 'pending'),
                        'metadata': user.get('metadata', {})
                    })
                except Exception as e:
                    logger.error(f"Error processing user {user.get('discord_id')}: {e}")
            return users
        except Exception as e:
            logger.error(f"Error getting guild users: {e}")
            return []
    
    def get_guild_stats(self, guild_id: str) -> Dict[str, Any]:
        try:
            # Total verified
            total_resp = supabase.table('verified_users')\
                .select('id', count='exact')\
                .eq('guild_id', guild_id)\
                .execute()
            total = total_resp.count or 0
            
            # Restored
            restored_resp = supabase.table('verified_users')\
                .select('id', count='exact')\
                .eq('guild_id', guild_id)\
                .eq('restored', True)\
                .execute()
            restored = restored_resp.count or 0
            
            # Verified today
            today = datetime.now().date().isoformat()
            today_resp = supabase.table('verified_users')\
                .select('id', count='exact')\
                .eq('guild_id', guild_id)\
                .gte('verified_at', f'{today}T00:00:00')\
                .execute()
            today_count = today_resp.count or 0
            
            return {
                'total_verified': total,
                'restored': restored,
                'pending': total - restored,
                'verified_today': today_count
            }
        except Exception as e:
            logger.error(f"Error getting guild stats: {e}")
            return {'total_verified': 0, 'restored': 0, 'pending': 0, 'verified_today': 0}
    
    def mark_user_restored(self, discord_id: str, guild_id: str, role_id: str = None):
        try:
            data = {
                'restored': True,
                'restored_at': datetime.now().isoformat(),
                'restored_role_id': role_id,
                'status': 'restored'
            }
            supabase.table('verified_users')\
                .update(data)\
                .eq('discord_id', discord_id)\
                .eq('guild_id', guild_id)\
                .execute()
        except Exception as e:
            logger.error(f"Error marking user restored: {e}")
    
    # Configuration Management
    def save_bot_config(self, user_id: str, config: Dict[str, Any]):
        try:
            data = {
                'user_id': user_id,
                'config': json.dumps(config),
                'updated_at': datetime.now().isoformat()
            }
            supabase.table('bot_configs').upsert(data, on_conflict='user_id').execute()
        except Exception as e:
            logger.error(f"Error saving bot config: {e}")
    
    def get_bot_config(self, user_id: str) -> Dict[str, Any]:
        try:
            response = supabase.table('bot_configs')\
                .select('config')\
                .eq('user_id', user_id)\
                .execute()
            
            if response.data:
                return json.loads(response.data[0]['config'])
        except Exception as e:
            logger.error(f"Error getting bot config: {e}")
        return {}
    
    def save_server_config(self, guild_id: str, config: Dict[str, Any]):
        try:
            data = {
                'guild_id': guild_id,
                'config': json.dumps(config),
                'updated_at': datetime.now().isoformat()
            }
            supabase.table('server_configs').upsert(data, on_conflict='guild_id').execute()
        except Exception as e:
            logger.error(f"Error saving server config: {e}")
    
    def get_server_config(self, guild_id: str) -> Dict[str, Any]:
        try:
            response = supabase.table('server_configs')\
                .select('config')\
                .eq('guild_id', guild_id)\
                .execute()
            
            if response.data:
                return json.loads(response.data[0]['config'])
        except Exception as e:
            logger.error(f"Error getting server config: {e}")
        return {}
    
    # Log Management
    def add_log(self, guild_id: str, log_type: str, message: str, user_id: str = None):
        try:
            data = {
                'guild_id': guild_id,
                'type': log_type,
                'message': message,
                'user_id': user_id,
                'created_at': datetime.now().isoformat()
            }
            supabase.table('logs').insert(data).execute()
        except Exception as e:
            logger.error(f"Error adding log: {e}")
    
    def get_logs(self, guild_id: str, log_type: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        try:
            query = supabase.table('logs')\
                .select('*')\
                .eq('guild_id', guild_id)\
                .order('created_at', desc=True)\
                .limit(limit)
            
            if log_type and log_type != 'all':
                query = query.eq('type', log_type)
            
            response = query.execute()
            return response.data
        except Exception as e:
            logger.error(f"Error getting logs: {e}")
            return []

# OAuth Handler
class OAuthHandler:
    def __init__(self):
        self.client_id = os.environ.get('DISCORD_CLIENT_ID')
        self.client_secret = os.environ.get('DISCORD_CLIENT_SECRET')
        self.redirect_uri = os.environ.get('REDIRECT_URI', 'https://bot-hosting-b.onrender.com/oauth/callback')
        self.bot_token = os.environ.get('DISCORD_BOT_TOKEN')
        
        if not all([self.client_id, self.client_secret]):
            logger.error("Missing OAuth configuration!")
            raise ValueError("OAuth credentials are required")
    
    async def exchange_code(self, code: str) -> Optional[Dict[str, Any]]:
        try:
            data = {
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': self.redirect_uri,
                'scope': 'identify guilds guilds.join'
            }
            
            headers = {'Content-Type': 'application/x-www-form-urlencoded'}
            
            async with aiohttp.ClientSession() as session:
                async with session.post('https://discord.com/api/oauth2/token', data=data, headers=headers) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        logger.info("Successfully exchanged code for tokens")
                        return result
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
    
    async def get_user_guilds(self, access_token: str) -> List[Dict[str, Any]]:
        try:
            headers = {'Authorization': f'Bearer {access_token}'}
            async with aiohttp.ClientSession() as session:
                async with session.get('https://discord.com/api/users/@me/guilds', headers=headers) as resp:
                    if resp.status == 200:
                        guilds = await resp.json()
                        return guilds
        except Exception as e:
            logger.error(f"Error getting user guilds: {e}")
        return []

# Initialize handlers
db = DatabaseManager()
oauth = OAuthHandler()

# Helper functions
def create_jwt(user_data: Dict[str, Any]) -> str:
    payload = {
        'sub': user_data['id'],
        'username': user_data['username'],
        'avatar': user_data.get('avatar'),
        'exp': datetime.now() + timedelta(days=7)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')

def verify_jwt(token: str) -> Optional[Dict[str, Any]]:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        return payload
    except jwt.PyJWTError:
        return None

async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Dict[str, Any]:
    token = credentials.credentials
    user_data = verify_jwt(token)
    if not user_data:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user_data

# Routes
@app.get("/")
async def root():
    return JSONResponse({
        "status": "online",
        "service": "xotiicsverify API",
        "version": "3.0.0",
        "dashboard": True,
        "endpoints": {
            "auth": "/api/auth/discord",
            "dashboard": "/api/dashboard/*",
            "bot": "/api/bot/*",
            "callback": "/oauth/callback",
            "health": "/health"
        }
    })

@app.get("/health")
async def health_check():
    try:
        supabase.table('verified_users').select('id').limit(1).execute()
        return {
            "status": "healthy",
            "database": "connected",
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=500, detail="Database connection failed")

# Authentication endpoints
@app.get("/api/auth/discord")
async def discord_auth_endpoint(redirect_url: str = None):
    """Handle frontend Discord authentication"""
    try:
        state = secrets.token_urlsafe(32)
        
        db.save_oauth_state(
            state=state,
            redirect_url=redirect_url
        )
        
        params = {
            'client_id': oauth.client_id,
            'redirect_uri': oauth.redirect_uri,
            'response_type': 'code',
            'scope': 'identify guilds',
            'state': state,
            'prompt': 'none'
        }
        
        auth_url = f"https://discord.com/api/oauth2/authorize?{urllib.parse.urlencode(params)}"
        return RedirectResponse(auth_url)
        
    except Exception as e:
        logger.error(f"Discord auth error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/auth/callback")
async def auth_callback(code: str, state: str):
    """Handle dashboard login callback"""
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
        
        user_guilds = await oauth.get_user_guilds(token_data['access_token'])
        
        user_data = {
            'id': user_info['id'],
            'username': f"{user_info['username']}#{user_info.get('discriminator', '0')}",
            'avatar': user_info.get('avatar'),
            'email': user_info.get('email'),
            'guilds': user_guilds
        }
        
        # Create JWT token
        jwt_token = create_jwt(user_data)
        
        redirect_url = state_data.get('redirect_url', 'https://bothostingf.vercel.app')
        return RedirectResponse(f"{redirect_url}?token={jwt_token}")
        
    except Exception as e:
        logger.error(f"Auth callback error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

# User verification endpoint (for Discord server verification)
@app.get("/oauth/callback")
async def oauth_callback(code: str, state: str):
    """Handle OAuth callback and add user to guild"""
    try:
        logger.info(f"Processing OAuth callback with state: {state[:10]}...")
        
        state_data = db.get_oauth_state(state)
        if not state_data:
            return HTMLResponse("""
                <html>
                    <head><title>Invalid Session</title></head>
                    <body style="background: #1a1a2e; color: white; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0;">
                        <div style="text-align: center; background: rgba(255,0,0,0.1); padding: 40px; border-radius: 20px; border: 2px solid #f72585;">
                            <h1>❌ Invalid Session</h1>
                            <p>Verification session expired or invalid.</p>
                            <p>Please try again from your Discord server.</p>
                        </div>
                    </body>
                </html>
            """, status_code=400)
        
        guild_id = state_data.get('guild_id')
        if not guild_id:
            return HTMLResponse("""
                <html>
                    <head><title>Missing Server</title></head>
                    <body style="background: #1a1a2e; color: white; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0;">
                        <div style="text-align: center; background: rgba(255,165,0,0.1); padding: 40px; border-radius: 20px; border: 2px solid #ffa500;">
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
                    <head><title>Authentication Failed</title></head>
                    <body style="background: #1a1a2e; color: white; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0;">
                        <div style="text-align: center; background: rgba(255,0,0,0.1); padding: 40px; border-radius: 20px; border: 2px solid #f72585;">
                            <h1>❌ Authentication Failed</h1>
                            <p>Could not verify your Discord account.</p>
                        </div>
                    </body>
                </html>
            """, status_code=400)
        
        user_info = await oauth.get_user_info(token_data['access_token'])
        if not user_info:
            return HTMLResponse("""
                <html>
                    <head><title>User Info Failed</title></head>
                    <body style="background: #1a1a2e; color: white; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0;">
                        <div style="text-align: center; background: rgba(255,0,0,0.1); padding: 40px; border-radius: 20px; border: 2px solid #f72585;">
                            <h1>❌ Could Not Get User Info</h1>
                            <p>Please try again.</p>
                        </div>
                    </body>
                </html>
            """, status_code=400)
        
        username = f"{user_info['username']}#{user_info.get('discriminator', '0')}"
        
        # Save user to database
        db.add_verified_user(
            discord_id=user_info['id'],
            username=username,
            access_token=token_data['access_token'],
            refresh_token=token_data['refresh_token'],
            expires_in=token_data['expires_in'],
            guild_id=guild_id,
            metadata={
                'avatar': user_info.get('avatar'),
                'email': user_info.get('email')
            }
        )
        
        # Add log entry
        db.add_log(
            guild_id=guild_id,
            log_type='verification',
            message=f'User {username} verified successfully',
            user_id=user_info['id']
        )
        
        # Try to add user to guild using bot
        bot_token = os.environ.get('DISCORD_BOT_TOKEN')
        added_to_guild = False
        
        if bot_token:
            headers = {
                'Authorization': f'Bot {bot_token}',
                'Content-Type': 'application/json'
            }
            
            data = {'access_token': token_data['access_token']}
            url = f'https://discord.com/api/guilds/{guild_id}/members/{user_info["id"]}'
            
            async with aiohttp.ClientSession() as session:
                async with session.put(url, headers=headers, json=data) as resp:
                    if resp.status in [200, 201, 204]:
                        logger.info(f"Added user {username} to guild {guild_id}")
                        added_to_guild = True
                        
                        # Mark as restored
                        db.mark_user_restored(user_info['id'], guild_id)
                        
                        # Add log
                        db.add_log(
                            guild_id=guild_id,
                            log_type='restoration',
                            message=f'User {username} added to guild',
                            user_id=user_info['id']
                        )
                    else:
                        logger.warning(f"Could not add user to guild: {resp.status}")
                        added_to_guild = False
        
        # Return success page
        success_html = f"""
        <html>
        <head>
            <title>Verification Successful</title>
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
                }}
                .container {{
                    text-align: center;
                    background: rgba(0, 0, 0, 0.3);
                    padding: 60px 40px;
                    border-radius: 20px;
                    backdrop-filter: blur(10px);
                    border: 2px solid #4ade80;
                    max-width: 500px;
                }}
                h1 {{ font-size: 3em; margin: 0 0 20px 0; }}
                .success {{ color: #4ade80; font-weight: bold; }}
                .info {{ color: #60a5fa; margin: 15px 0; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>✅ Verification Successful!</h1>
                <p class="success">Welcome, {username}!</p>
                <p class="info">Your account has been verified and saved.</p>
                {"<p class='info'>You have been added to the server!</p>" if added_to_guild else "<p class='info'>An admin will restore your access soon.</p>"}
                <p class="info">You can now close this window.</p>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(success_html)
        
    except Exception as e:
        logger.error(f"OAuth callback error: {e}", exc_info=True)
        db.add_log(
            guild_id=guild_id if 'guild_id' in locals() else 'unknown',
            log_type='error',
            message=f'Verification error: {str(e)}'
        )
        return HTMLResponse("""
            <html>
                <head><title>Error</title></head>
                <body style="background: #1a1a2e; color: white; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0;">
                    <div style="text-align: center; background: rgba(255,0,0,0.1); padding: 40px; border-radius: 20px; border: 2px solid #f72585;">
                        <h1>❌ An Error Occurred</h1>
                        <p>Something went wrong during verification.</p>
                        <p>Please try again from your Discord server.</p>
                    </div>
                </body>
            </html>
        """, status_code=500)

# Dashboard API endpoints
@app.get("/api/dashboard/user")
async def get_dashboard_user(user: Dict[str, Any] = Depends(verify_token)):
    """Get current user data for dashboard"""
    return {
        "success": True,
        "user": {
            "id": user.get('sub'),
            "username": user.get('username'),
            "avatar": user.get('avatar')
        }
    }

@app.get("/api/dashboard/servers")
async def get_user_servers(user: Dict[str, Any] = Depends(verify_token)):
    """Get user's guilds with bot status"""
    try:
        # Get guilds where bot is present (this would require bot to have access)
        # For now, return mock data or use Discord API
        return {
            "success": True,
            "servers": [
                {
                    "id": "123456789",
                    "name": "Test Server",
                    "icon": None,
                    "permissions": 8,
                    "bot_member": True
                }
            ]
        }
    except Exception as e:
        logger.error(f"Error getting user servers: {e}")
        raise HTTPException(status_code=500, detail="Failed to get servers")

@app.get("/api/dashboard/server/{guild_id}/stats")
async def get_server_stats(guild_id: str, user: Dict[str, Any] = Depends(verify_token)):
    """Get server statistics"""
    stats = db.get_guild_stats(guild_id)
    return {
        "success": True,
        "stats": stats
    }

@app.get("/api/dashboard/server/{guild_id}/members")
async def get_server_members(
    guild_id: str,
    status: str = None,
    limit: int = 50,
    offset: int = 0,
    user: Dict[str, Any] = Depends(verify_token)
):
    """Get server members with pagination"""
    members = db.get_guild_users(guild_id, status, limit, offset)
    return {
        "success": True,
        "members": members,
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total": len(members)
        }
    }

@app.post("/api/dashboard/server/{guild_id}/restore")
async def restore_members(
    guild_id: str,
    request: Request,
    user: Dict[str, Any] = Depends(verify_token)
):
    """Restore members to server"""
    try:
        data = await request.json()
        member_ids = data.get('member_ids', [])
        role_id = data.get('role_id')
        
        # In a real implementation, this would call the bot to add members
        # For now, just mark them as restored in database
        
        restored_count = 0
        for member_id in member_ids:
            db.mark_user_restored(member_id, guild_id, role_id)
            restored_count += 1
        
        # Add log
        db.add_log(
            guild_id=guild_id,
            log_type='restoration',
            message=f'Restored {restored_count} members to server',
            user_id=user.get('sub')
        )
        
        return {
            "success": True,
            "message": f"Restored {restored_count} members",
            "restored_count": restored_count
        }
    except Exception as e:
        logger.error(f"Error restoring members: {e}")
        raise HTTPException(status_code=500, detail="Failed to restore members")

@app.get("/api/dashboard/server/{guild_id}/config")
async def get_server_config(guild_id: str, user: Dict[str, Any] = Depends(verify_token)):
    """Get server configuration"""
    config = db.get_server_config(guild_id)
    return {
        "success": True,
        "config": config
    }

@app.post("/api/dashboard/server/{guild_id}/config")
async def update_server_config(
    guild_id: str,
    request: Request,
    user: Dict[str, Any] = Depends(verify_token)
):
    """Update server configuration"""
    try:
        data = await request.json()
        db.save_server_config(guild_id, data)
        
        # Add log
        db.add_log(
            guild_id=guild_id,
            log_type='config',
            message='Server configuration updated',
            user_id=user.get('sub')
        )
        
        return {
            "success": True,
            "message": "Configuration saved"
        }
    except Exception as e:
        logger.error(f"Error saving server config: {e}")
        raise HTTPException(status_code=500, detail="Failed to save configuration")

@app.get("/api/dashboard/bot/config")
async def get_bot_config(user: Dict[str, Any] = Depends(verify_token)):
    """Get bot configuration"""
    config = db.get_bot_config(user.get('sub'))
    return {
        "success": True,
        "config": config
    }

@app.post("/api/dashboard/bot/config")
async def update_bot_config(
    request: Request,
    user: Dict[str, Any] = Depends(verify_token)
):
    """Update bot configuration"""
    try:
        data = await request.json()
        db.save_bot_config(user.get('sub'), data)
        
        return {
            "success": True,
            "message": "Bot configuration saved"
        }
    except Exception as e:
        logger.error(f"Error saving bot config: {e}")
        raise HTTPException(status_code=500, detail="Failed to save configuration")

@app.get("/api/dashboard/server/{guild_id}/logs")
async def get_server_logs(
    guild_id: str,
    log_type: str = None,
    limit: int = 100,
    user: Dict[str, Any] = Depends(verify_token)
):
    """Get server logs"""
    logs = db.get_logs(guild_id, log_type, limit)
    return {
        "success": True,
        "logs": logs
    }

# Bot API endpoints (for Discord bot to call)
@app.get("/api/bot/status")
async def get_bot_status():
    """Get bot status"""
    return {
        "status": "online",
        "timestamp": datetime.now().isoformat(),
        "version": "3.0.0"
    }

@app.get("/api/bot/guild/{guild_id}/verified")
async def get_guild_verified_users(guild_id: str):
    """Get verified users for a guild (for bot restoration)"""
    users = db.get_guild_users(guild_id)
    return {
        "success": True,
        "users": users
    }

@app.post("/api/bot/guild/{guild_id}/restore")
async def bot_restore_members(guild_id: str, request: Request):
    """Bot endpoint to restore members"""
    try:
        data = await request.json()
        member_ids = data.get('member_ids', [])
        role_id = data.get('role_id')
        
        restored_count = 0
        for member_id in member_ids:
            db.mark_user_restored(member_id, guild_id, role_id)
            restored_count += 1
        
        return {
            "success": True,
            "restored_count": restored_count
        }
    except Exception as e:
        logger.error(f"Bot restore error: {e}")
        raise HTTPException(status_code=500, detail="Restoration failed")

# Utility endpoints
@app.get("/api/verify/{guild_id}")
async def get_verification_url(guild_id: str):
    """Get verification URL for a specific guild"""
    api_url = os.environ.get('API_URL', 'https://bot-hosting-b.onrender.com')
    frontend_url = os.environ.get('FRONTEND_URL', 'https://bothostingf.vercel.app')
    
    verification_url = f"{api_url}/api/auth/discord?redirect_url={urllib.parse.quote(frontend_url)}?guild_id={guild_id}"
    
    return {
        "success": True,
        "verification_url": verification_url,
        "embed_code": f"[Verify Here]({verification_url})"
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    logger.info(f"Starting API server on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False)
