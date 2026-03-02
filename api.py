from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict
import json
import hashlib
import secrets
import sqlite3
import asyncio
from datetime import datetime, timedelta
import random

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database setup
def init_db():
    conn = sqlite3.connect('xbatch.db')
    c = conn.cursor()
    
    # Users table
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Characters table
    c.execute('''
        CREATE TABLE IF NOT EXISTS characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            class TEXT NOT NULL,
            level INTEGER DEFAULT 1,
            exp INTEGER DEFAULT 0,
            exp_needed INTEGER DEFAULT 100,
            max_hp INTEGER NOT NULL,
            current_hp INTEGER NOT NULL,
            max_mana INTEGER DEFAULT 50,
            current_mana INTEGER DEFAULT 50,
            base_attack INTEGER NOT NULL,
            base_defense INTEGER NOT NULL,
            magic INTEGER DEFAULT 0,
            crit_chance INTEGER DEFAULT 5,
            crit_damage INTEGER DEFAULT 150,
            gold INTEGER DEFAULT 100,
            kills INTEGER DEFAULT 0,
            deaths INTEGER DEFAULT 0,
            current_location TEXT DEFAULT 'village',
            last_saved TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # Inventory table
    c.execute('''
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            character_id INTEGER NOT NULL,
            item_id TEXT NOT NULL,
            item_name TEXT NOT NULL,
            quantity INTEGER DEFAULT 1,
            item_type TEXT NOT NULL,
            FOREIGN KEY (character_id) REFERENCES characters (id)
        )
    ''')
    
    # Tokens table (for auth)
    c.execute('''
        CREATE TABLE IF NOT EXISTS tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    conn.commit()
    conn.close()

init_db()

# Game Constants
CLASS_BASE_STATS = {
    'warrior': {'hp': 120, 'mana': 30, 'attack': 15, 'defense': 12, 'magic': 0, 'crit': 5},
    'mage': {'hp': 70, 'mana': 100, 'attack': 5, 'defense': 5, 'magic': 20, 'crit': 10},
    'rogue': {'hp': 90, 'mana': 50, 'attack': 12, 'defense': 8, 'magic': 5, 'crit': 20},
    'paladin': {'hp': 110, 'mana': 60, 'attack': 12, 'defense': 15, 'magic': 8, 'crit': 5},
    'ranger': {'hp': 95, 'mana': 55, 'attack': 14, 'defense': 9, 'magic': 3, 'crit': 15}
}

LOCATIONS = {
    'village': {
        'name': 'Village of Beginnings',
        'description': 'A peaceful village where your journey starts.',
        'enemies': [],
        'merchants': ['general_store']
    },
    'forest': {
        'name': 'Darkwood Forest',
        'description': 'A dense forest filled with dangerous creatures.',
        'enemies': ['slime', 'goblin', 'wolf'],
        'required_level': 2
    },
    'mountains': {
        'name': 'Crystal Mountains',
        'description': 'Towering peaks rich with minerals and danger.',
        'enemies': ['bear', 'orc', 'troll'],
        'required_level': 5
    }
}

ENEMIES = {
    'slime': {
        'name': 'Slime',
        'level': 1,
        'max_hp': 30,
        'attack': 5,
        'defense': 2,
        'exp_reward': 10,
        'gold_reward': 5,
        'abilities': []
    },
    'goblin': {
        'name': 'Goblin',
        'level': 2,
        'max_hp': 45,
        'attack': 8,
        'defense': 4,
        'exp_reward': 20,
        'gold_reward': 10,
        'abilities': []
    },
    'wolf': {
        'name': 'Wolf',
        'level': 3,
        'max_hp': 60,
        'attack': 12,
        'defense': 5,
        'exp_reward': 30,
        'gold_reward': 15,
        'abilities': []
    },
    'bear': {
        'name': 'Bear',
        'level': 5,
        'max_hp': 120,
        'attack': 18,
        'defense': 10,
        'exp_reward': 60,
        'gold_reward': 30,
        'abilities': []
    },
    'orc': {
        'name': 'Orc Warrior',
        'level': 7,
        'max_hp': 150,
        'attack': 22,
        'defense': 15,
        'exp_reward': 100,
        'gold_reward': 50,
        'abilities': [{'name': 'Berserk', 'damage_multiplier': 1.5}]
    },
    'troll': {
        'name': 'Troll',
        'level': 10,
        'max_hp': 250,
        'attack': 30,
        'defense': 20,
        'exp_reward': 200,
        'gold_reward': 100,
        'abilities': [{'name': 'Regeneration'}]
    }
}

ITEMS = {
    'health_potion': {
        'name': 'Health Potion',
        'type': 'consumable',
        'value': 50,
        'effect': {'heal_hp': 50}
    },
    'mana_potion': {
        'name': 'Mana Potion',
        'type': 'consumable',
        'value': 40,
        'effect': {'heal_mana': 30}
    },
    'iron_sword': {
        'name': 'Iron Sword',
        'type': 'weapon',
        'value': 100,
        'stats': {'attack': 5}
    }
}

# Models
class UserCreate(BaseModel):
    username: str
    password: str
    class_: str

class UserLogin(BaseModel):
    username: str
    password: str

class CombatAction(BaseModel):
    action: str

# WebSocket Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, WebSocket] = {}
        self.user_locations: Dict[int, str] = {}

    async def connect(self, websocket: WebSocket, user_id: int):
        await websocket.accept()
        self.active_connections[user_id] = websocket
        await self.broadcast_presence()

    def disconnect(self, user_id: int):
        if user_id in self.active_connections:
            del self.active_connections[user_id]
        if user_id in self.user_locations:
            del self.user_locations[user_id]

    async def broadcast_presence(self):
        count = len(self.active_connections)
        for connection in self.active_connections.values():
            try:
                await connection.send_json({
                    'type': 'presence',
                    'count': count
                })
            except:
                pass

    async def send_chat(self, user_id: int, username: str, message: str, channel: str):
        for uid, connection in self.active_connections.items():
            try:
                await connection.send_json({
                    'type': 'chat',
                    'username': username,
                    'message': message,
                    'channel': channel,
                    'timestamp': datetime.now().isoformat()
                })
            except:
                pass

manager = ConnectionManager()

# Helper Functions
def hash_password(password: str, salt: str = None) -> tuple:
    if not salt:
        salt = secrets.token_hex(16)
    hash_obj = hashlib.sha256((password + salt).encode())
    return hash_obj.hexdigest(), salt

def generate_token(user_id: int) -> str:
    token = secrets.token_hex(32)
    expires = datetime.now() + timedelta(days=7)
    
    conn = sqlite3.connect('xbatch.db')
    c = conn.cursor()
    c.execute('INSERT INTO tokens (user_id, token, expires_at) VALUES (?, ?, ?)',
              (user_id, token, expires.isoformat()))
    conn.commit()
    conn.close()
    
    return token

def verify_token(token: str) -> Optional[int]:
    conn = sqlite3.connect('xbatch.db')
    c = conn.cursor()
    c.execute('SELECT user_id, expires_at FROM tokens WHERE token = ?', (token,))
    result = c.fetchone()
    conn.close()
    
    if result:
        user_id, expires = result
        if datetime.now() < datetime.fromisoformat(expires):
            return user_id
    return None

def get_user_from_token(token: str) -> Optional[dict]:
    user_id = verify_token(token)
    if not user_id:
        return None
    
    conn = sqlite3.connect('xbatch.db')
    c = conn.cursor()
    c.execute('SELECT id, username, created_at FROM users WHERE id = ?', (user_id,))
    user = c.fetchone()
    conn.close()
    
    if user:
        return {'id': user[0], 'username': user[1], 'created_at': user[2]}
    return None

# Auth Endpoints
@app.post('/api/auth/register')
async def register(user_data: UserCreate):
    conn = sqlite3.connect('xbatch.db')
    c = conn.cursor()
    
    # Check if user exists
    c.execute('SELECT id FROM users WHERE username = ?', (user_data.username,))
    if c.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail='Username already exists')
    
    # Create user
    password_hash, salt = hash_password(user_data.password)
    c.execute('INSERT INTO users (username, password_hash, salt) VALUES (?, ?, ?)',
              (user_data.username, password_hash, salt))
    user_id = c.lastrowid
    
    # Create character
    stats = CLASS_BASE_STATS[user_data.class_]
    c.execute('''
        INSERT INTO characters 
        (user_id, name, class, max_hp, current_hp, max_mana, current_mana, 
         base_attack, base_defense, magic, crit_chance)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, user_data.username, user_data.class_, 
          stats['hp'], stats['hp'], stats['mana'], stats['mana'],
          stats['attack'], stats['defense'], stats['magic'], stats['crit']))
    
    # Add starting items
    char_id = c.lastrowid
    c.execute('''
        INSERT INTO inventory (character_id, item_id, item_name, quantity, item_type)
        VALUES (?, ?, ?, ?, ?)
    ''', (char_id, 'health_potion', 'Health Potion', 3, 'consumable'))
    
    c.execute('''
        INSERT INTO inventory (character_id, item_id, item_name, quantity, item_type)
        VALUES (?, ?, ?, ?, ?)
    ''', (char_id, 'mana_potion', 'Mana Potion', 2, 'consumable'))
    
    conn.commit()
    conn.close()
    
    return {'message': 'User created successfully'}

@app.post('/api/auth/login')
async def login(login_data: UserLogin):
    conn = sqlite3.connect('xbatch.db')
    c = conn.cursor()
    
    # Get user
    c.execute('SELECT id, password_hash, salt FROM users WHERE username = ?', 
              (login_data.username,))
    result = c.fetchone()
    
    if not result:
        conn.close()
        raise HTTPException(status_code=401, detail='Invalid username or password')
    
    user_id, stored_hash, salt = result
    
    # Verify password
    input_hash, _ = hash_password(login_data.password, salt)
    if input_hash != stored_hash:
        conn.close()
        raise HTTPException(status_code=401, detail='Invalid username or password')
    
    # Get character
    c.execute('SELECT * FROM characters WHERE user_id = ?', (user_id,))
    character = c.fetchone()
    
    # Get inventory
    c.execute('SELECT * FROM inventory WHERE character_id = ?', (character[0],))
    inventory = c.fetchall()
    
    conn.close()
    
    # Generate token
    token = generate_token(user_id)
    
    return {
        'token': token,
        'user': {'id': user_id, 'username': login_data.username},
        'character': {
            'id': character[0],
            'name': character[2],
            'class': character[3],
            'level': character[4],
            'exp': character[5],
            'exp_needed': character[6],
            'max_hp': character[7],
            'current_hp': character[8],
            'max_mana': character[9],
            'current_mana': character[10],
            'base_attack': character[11],
            'base_defense': character[12],
            'magic': character[13],
            'crit_chance': character[14],
            'crit_damage': character[15],
            'gold': character[16],
            'kills': character[17],
            'deaths': character[18],
            'current_location': character[19]
        },
        'inventory': [
            {
                'id': i[0],
                'item_id': i[3],
                'item_name': i[4],
                'quantity': i[5],
                'item_type': i[6]
            } for i in inventory
        ]
    }

# Game Endpoints
@app.get('/api/game/state')
async def get_game_state(token: str):
    user = get_user_from_token(token)
    if not user:
        raise HTTPException(status_code=401, detail='Invalid token')
    
    conn = sqlite3.connect('xbatch.db')
    c = conn.cursor()
    
    # Get character
    c.execute('SELECT * FROM characters WHERE user_id = ?', (user['id'],))
    character = c.fetchone()
    
    # Get inventory
    c.execute('SELECT * FROM inventory WHERE character_id = ?', (character[0],))
    inventory = c.fetchall()
    
    conn.close()
    
    return {
        'user': user,
        'character': {
            'id': character[0],
            'name': character[2],
            'class': character[3],
            'level': character[4],
            'exp': character[5],
            'exp_needed': character[6],
            'max_hp': character[7],
            'current_hp': character[8],
            'max_mana': character[9],
            'current_mana': character[10],
            'base_attack': character[11],
            'base_defense': character[12],
            'magic': character[13],
            'crit_chance': character[14],
            'crit_damage': character[15],
            'gold': character[16],
            'kills': character[17],
            'deaths': character[18],
            'current_location': character[19]
        },
        'inventory': [
            {
                'id': i[0],
                'item_id': i[3],
                'item_name': i[4],
                'quantity': i[5],
                'item_type': i[6]
            } for i in inventory
        ]
    }

@app.get('/api/game/locations')
async def get_locations():
    return [
        {'id': loc_id, 'name': loc['name'], 'description': loc['description'], 
         'enemies': loc.get('enemies', [])}
        for loc_id, loc in LOCATIONS.items()
    ]

@app.get('/api/game/location/{location_id}')
async def get_location(location_id: str):
    if location_id not in LOCATIONS:
        raise HTTPException(status_code=404, detail='Location not found')
    
    location = LOCATIONS[location_id].copy()
    
    # Add enemy details
    if location.get('enemies'):
        location['enemies'] = [
            {**ENEMIES[enemy_id], 'id': enemy_id}
            for enemy_id in location['enemies']
        ]
    
    return location

@app.post('/api/game/combat/start')
async def start_combat(data: dict, token: str):
    user = get_user_from_token(token)
    if not user:
        raise HTTPException(status_code=401, detail='Invalid token')
    
    enemy_id = data.get('enemy_id')
    if enemy_id not in ENEMIES:
        raise HTTPException(status_code=404, detail='Enemy not found')
    
    enemy = ENEMIES[enemy_id].copy()
    enemy['current_hp'] = enemy['max_hp']
    
    return {
        'in_combat': True,
        'current_enemy': {**enemy, 'id': enemy_id},
        'combat_log': [{
            'round': 0,
            'message': f'Combat started with {enemy["name"]}!',
            'type': 'system'
        }],
        'turn': 'player',
        'round': 1
    }

@app.post('/api/game/combat/action')
async def combat_action(data: dict, token: str):
    user = get_user_from_token(token)
    if not user:
        raise HTTPException(status_code=401, detail='Invalid token')
    
    action = data.get('action')
    
    # Simple combat simulation
    enemy = data.get('enemy')
    if not enemy:
        raise HTTPException(status_code=400, detail='No enemy data')
    
    if action == 'attack':
        # Player attacks
        damage = random.randint(10, 20)
        enemy['current_hp'] -= damage
        
        log = [{
            'round': 1,
            'message': f'You attack for {damage} damage!',
            'type': 'player_attack'
        }]
        
        if enemy['current_hp'] <= 0:
            # Victory
            exp_gained = enemy['exp_reward']
            gold_gained = enemy['gold_reward']
            
            # Update character in database
            conn = sqlite3.connect('xbatch.db')
            c = conn.cursor()
            c.execute('''
                UPDATE characters 
                SET exp = exp + ?, gold = gold + ?, kills = kills + 1
                WHERE user_id = ?
            ''', (exp_gained, gold_gained, user['id']))
            conn.commit()
            conn.close()
            
            return {
                'combat_ended': True,
                'victory': True,
                'exp_gained': exp_gained,
                'gold_gained': gold_gained
            }
        
        # Enemy counter-attack
        enemy_damage = random.randint(5, 15)
        log.append({
            'round': 1,
            'message': f'{enemy["name"]} attacks for {enemy_damage} damage!',
            'type': 'enemy_attack'
        })
        
        return {
            'combat_ended': False,
            'current_enemy': enemy,
            'combat_log': log,
            'turn': 'player',
            'round': 2
        }
    
    elif action == 'flee':
        if random.random() < 0.5:
            return {
                'combat_ended': True,
                'victory': False,
                'fled': True
            }
        else:
            # Failed to flee, enemy attacks
            enemy_damage = random.randint(5, 15)
            return {
                'combat_ended': False,
                'current_enemy': enemy,
                'combat_log': [{
                    'round': 1,
                    'message': f'Failed to flee! {enemy["name"]} attacks for {enemy_damage} damage!',
                    'type': 'enemy_attack'
                }],
                'turn': 'player',
                'round': 2
            }

@app.post('/api/game/item/use')
async def use_item(data: dict, token: str):
    user = get_user_from_token(token)
    if not user:
        raise HTTPException(status_code=401, detail='Invalid token')
    
    item_id = data.get('item_id')
    
    conn = sqlite3.connect('xbatch.db')
    c = conn.cursor()
    
    # Get item
    c.execute('''
        SELECT i.*, c.* FROM inventory i
        JOIN characters c ON c.id = i.character_id
        WHERE i.item_id = ? AND c.user_id = ?
    ''', (item_id, user['id']))
    result = c.fetchone()
    
    if not result:
        conn.close()
        raise HTTPException(status_code=404, detail='Item not found')
    
    # Use item (simplified - just heal for potions)
    if item_id == 'health_potion':
        c.execute('''
            UPDATE characters 
            SET current_hp = MIN(max_hp, current_hp + 50)
            WHERE user_id = ?
        ''', (user['id'],))
        
        # Remove one potion
        c.execute('''
            UPDATE inventory 
            SET quantity = quantity - 1 
            WHERE item_id = ? AND character_id = (SELECT id FROM characters WHERE user_id = ?)
        ''', (item_id, user['id']))
        
        # Delete if quantity 0
        c.execute('''
            DELETE FROM inventory 
            WHERE item_id = ? AND quantity <= 0
        ''', (item_id,))
    
    conn.commit()
    
    # Get updated character and inventory
    c.execute('SELECT * FROM characters WHERE user_id = ?', (user['id'],))
    character = c.fetchone()
    
    c.execute('''
        SELECT * FROM inventory 
        WHERE character_id = (SELECT id FROM characters WHERE user_id = ?)
    ''', (user['id'],))
    inventory = c.fetchall()
    
    conn.close()
    
    return {
        'success': True,
        'message': f'Used {ITEMS[item_id]["name"]}',
        'character': {
            'current_hp': character[8],
            'max_hp': character[7],
            'current_mana': character[10],
            'max_mana': character[9]
        },
        'inventory': [
            {
                'id': i[0],
                'item_id': i[3],
                'item_name': i[4],
                'quantity': i[5],
                'item_type': i[6]
            } for i in inventory
        ]
    }

@app.get('/api/game/online')
async def get_online_count():
    return {'count': len(manager.active_connections)}

@app.post('/api/game/save')
async def save_game(character_data: dict, token: str):
    user = get_user_from_token(token)
    if not user:
        raise HTTPException(status_code=401, detail='Invalid token')
    
    conn = sqlite3.connect('xbatch.db')
    c = conn.cursor()
    
    c.execute('''
        UPDATE characters 
        SET current_hp = ?, current_mana = ?, exp = ?, gold = ?,
            kills = ?, deaths = ?, current_location = ?, last_saved = CURRENT_TIMESTAMP
        WHERE user_id = ?
    ''', (character_data.get('current_hp'), character_data.get('current_mana'),
          character_data.get('exp'), character_data.get('gold'),
          character_data.get('kills'), character_data.get('deaths'),
          character_data.get('current_location'), user['id']))
    
    conn.commit()
    conn.close()
    
    return {'message': 'Game saved'}

# WebSocket endpoint
@app.websocket('/ws')
async def websocket_endpoint(websocket: WebSocket):
    token = websocket.query_params.get('token')
    user = get_user_from_token(token) if token else None
    
    if not user:
        await websocket.close(code=1008)
        return
    
    await manager.connect(websocket, user['id'])
    
    try:
        while True:
            data = await websocket.receive_json()
            
            if data['type'] == 'chat':
                await manager.send_chat(
                    user['id'],
                    user['username'],
                    data['message'],
                    data.get('channel', 'global')
                )
            
    except WebSocketDisconnect:
        manager.disconnect(user['id'])
        await manager.broadcast_presence()

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
