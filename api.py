from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import os
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import json
import aiohttp
import urllib.parse
from supabase import create_client, Client
from cryptography.fernet import Fernet
import base64

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
    version="2.0.0"
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Supabase
supabase_url = os.environ.get('SUPABASE_URL')
supabase_key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')

if not supabase_url or not supabase_key:
    logger.error("Missing Supabase credentials!")
    raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")

supabase: Client = create_client(supabase_url, supabase_key)

class DatabaseManager:
    def __init__(self):
        self.cipher_key = os.environ.get('ENCRYPTION_KEY')
        if not self.cipher_key:
            self.cipher_key = base64.urlsafe_b64encode(Fernet.generate_key())
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
                'restored': False
            }
            
            supabase.table('verified_users').upsert(data, on_conflict='discord_id,guild_id').execute()
            logger.info(f"Added verified user: {username} for guild {guild_id}")
            return True
        except Exception as e:
            logger.error(f"Error adding verified user: {e}")
            return False
    
    def mark_user_restored(self, discord_id: str, guild_id: str, role_id: str = None):
        try:
            data = {
                'restored': True,
                'restored_at': datetime.now().isoformat(),
                'restored_role_id': role_id
            }
            supabase.table('verified_users')\
                .update(data)\
                .eq('discord_id', discord_id)\
                .eq('guild_id', guild_id)\
                .execute()
        except Exception as e:
            logger.error(f"Error marking user restored: {e}")

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
                'scope': 'identify guilds.join'
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

# Initialize handlers
db = DatabaseManager()
oauth = OAuthHandler()

@app.get("/")
async def root():
    return JSONResponse({
        "status": "online",
        "service": "xotiicsverify API",
        "version": "2.0.0",
        "endpoints": {
            "auth": "/api/auth/discord",
            "callback": "/oauth/callback",
            "health": "/health"
        }
    })

@app.get("/health")
async def health_check():
    try:
        supabase.table('verified_users').select('id').limit(1).execute()
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=500, detail="Database connection failed")

@app.get("/api/auth/discord")
async def discord_auth_endpoint(redirect_url: str = None):
    """Handle frontend Discord authentication"""
    try:
        state = secrets.token_urlsafe(32)
        
        # Parse guild_id from redirect_url if present
        guild_id = None
        if redirect_url:
            try:
                parsed = urllib.parse.urlparse(redirect_url)
                query_params = urllib.parse.parse_qs(parsed.query)
                guild_id = query_params.get('guild_id', [None])[0]
            except:
                pass
        
        db.save_oauth_state(
            state=state,
            guild_id=guild_id,
            redirect_url=redirect_url
        )
        
        params = {
            'client_id': oauth.client_id,
            'redirect_uri': oauth.redirect_uri,
            'response_type': 'code',
            'scope': 'identify guilds.join',
            'state': state,
            'prompt': 'none'
        }
        
        auth_url = f"https://discord.com/api/oauth2/authorize?{urllib.parse.urlencode(params)}"
        return RedirectResponse(auth_url)
        
    except Exception as e:
        logger.error(f"Discord auth error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

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
        
        # Add user to guild using bot
        bot_token = os.environ.get('DISCORD_BOT_TOKEN')
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
                    else:
                        logger.warning(f"Could not add user to guild: {resp.status}")
                        added_to_guild = False
        
        return HTMLResponse(f"""
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
                        <p class="info">You should now have access to the server!</p>
                        <p class="info">An admin can use <code>/verify restore</code> to add you with a role.</p>
                        <p style="margin-top: 30px; font-size: 0.9em; opacity: 0.7;">You can close this window now.</p>
                    </div>
                </body>
            </html>
        """)
        
    except Exception as e:
        logger.error(f"OAuth callback error: {e}", exc_info=True)
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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    logger.info(f"Starting API server on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False)
