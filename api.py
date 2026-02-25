"""
Complete 2D Pixel RPG Backend
Single file FastAPI application with SQLite database
Includes Full Game Systems, Multiplayer, Trading, PvP, Guilds, and 5-Act Story
"""

import os
import json
import uuid
import hashlib
import secrets
import asyncio
import datetime
import math
import random
import sqlite3
from typing import Dict, List, Optional, Any, Set
from enum import Enum
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
import time

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse, FileResponse
import jwt
from pydantic import BaseModel, Field, validator
import bcrypt

# ==================== Configuration ====================

DATABASE_PATH = "rpg_game.db"
JWT_SECRET = os.getenv("JWT_SECRET", secrets.token_urlsafe(32))
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 1 week

# ==================== Database Setup ====================

def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    """Initialize all database tables"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            email TEXT,
            role TEXT DEFAULT 'player',
            is_owner BOOLEAN DEFAULT 0,
            banned BOOLEAN DEFAULT 0,
            ban_reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        )
    ''')
    
    # Characters table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT UNIQUE NOT NULL,
            class_name TEXT NOT NULL,
            level INTEGER DEFAULT 1,
            exp INTEGER DEFAULT 0,
            gold INTEGER DEFAULT 100,
            hp INTEGER DEFAULT 100,
            max_hp INTEGER DEFAULT 100,
            mana INTEGER DEFAULT 50,
            max_mana INTEGER DEFAULT 50,
            strength INTEGER DEFAULT 10,
            agility INTEGER DEFAULT 10,
            intelligence INTEGER DEFAULT 10,
            vitality INTEGER DEFAULT 10,
            x_position INTEGER DEFAULT 50,
            y_position INTEGER DEFAULT 50,
            map_id TEXT DEFAULT 'start_village',
            guild_id INTEGER,
            party_id INTEGER,
            role TEXT DEFAULT 'player',
            skill_points INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # Inventory table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            character_id INTEGER NOT NULL,
            item_id TEXT NOT NULL,
            quantity INTEGER DEFAULT 1,
            equipped BOOLEAN DEFAULT 0,
            slot INTEGER,
            FOREIGN KEY (character_id) REFERENCES characters (id)
        )
    ''')
    
    # Skills table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            character_id INTEGER NOT NULL,
            skill_id TEXT NOT NULL,
            level INTEGER DEFAULT 1,
            unlocked BOOLEAN DEFAULT 1,
            FOREIGN KEY (character_id) REFERENCES characters (id)
        )
    ''')
    
    # Quests table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            character_id INTEGER NOT NULL,
            quest_id TEXT NOT NULL,
            status TEXT DEFAULT 'not_started',
            progress INTEGER DEFAULT 0,
            objectives TEXT,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            FOREIGN KEY (character_id) REFERENCES characters (id)
        )
    ''')
    
    # Guilds table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS guilds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            tag TEXT UNIQUE NOT NULL,
            leader_id INTEGER NOT NULL,
            level INTEGER DEFAULT 1,
            exp INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (leader_id) REFERENCES characters (id)
        )
    ''')
    
    # Guild members table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS guild_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            character_id INTEGER NOT NULL,
            rank TEXT DEFAULT 'member',
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (guild_id) REFERENCES guilds (id),
            FOREIGN KEY (character_id) REFERENCES characters (id)
        )
    ''')
    
    # Parties table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS parties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            leader_id INTEGER NOT NULL,
            name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Party members table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS party_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            party_id INTEGER NOT NULL,
            character_id INTEGER NOT NULL,
            FOREIGN KEY (party_id) REFERENCES parties (id),
            FOREIGN KEY (character_id) REFERENCES characters (id)
        )
    ''')
    
    # Auctions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS auctions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id INTEGER NOT NULL,
            item_instance_id INTEGER NOT NULL,
            starting_bid INTEGER NOT NULL,
            current_bid INTEGER,
            current_bidder_id INTEGER,
            buyout_price INTEGER,
            status TEXT DEFAULT 'active',
            ends_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (seller_id) REFERENCES characters (id),
            FOREIGN KEY (current_bidder_id) REFERENCES characters (id)
        )
    ''')
    
    # Trades table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            initiator_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            initiator_items TEXT,
            target_items TEXT,
            initiator_gold INTEGER DEFAULT 0,
            target_gold INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            FOREIGN KEY (initiator_id) REFERENCES characters (id),
            FOREIGN KEY (target_id) REFERENCES characters (id)
        )
    ''')
    
    # PvP matches table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pvp_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player1_id INTEGER NOT NULL,
            player2_id INTEGER NOT NULL,
            winner_id INTEGER,
            status TEXT DEFAULT 'pending',
            match_data TEXT,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ended_at TIMESTAMP,
            FOREIGN KEY (player1_id) REFERENCES characters (id),
            FOREIGN KEY (player2_id) REFERENCES characters (id)
        )
    ''')
    
    # PvP stats table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pvp_stats (
            character_id INTEGER PRIMARY KEY,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            rating INTEGER DEFAULT 1000,
            FOREIGN KEY (character_id) REFERENCES characters (id)
        )
    ''')
    
    # Messages/Chat history
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL,
            channel TEXT NOT NULL,
            content TEXT NOT NULL,
            target_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (sender_id) REFERENCES characters (id)
        )
    ''')
    
    # Moderation logs
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mod_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            moderator_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            reason TEXT,
            duration INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (moderator_id) REFERENCES users (id),
            FOREIGN KEY (target_id) REFERENCES users (id)
        )
    ''')
    
    # User warnings
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            moderator_id INTEGER NOT NULL,
            reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (moderator_id) REFERENCES users (id)
        )
    ''')
    
    # Story progression
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS story_progression (
            character_id INTEGER PRIMARY KEY,
            current_act INTEGER DEFAULT 1,
            current_chapter INTEGER DEFAULT 1,
            completed_acts TEXT DEFAULT '[]',
            choices_made TEXT DEFAULT '[]',
            reputation JSON,
            companions TEXT DEFAULT '[]',
            FOREIGN KEY (character_id) REFERENCES characters (id)
        )
    ''')
    
    # Codex entries
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS codex (
            character_id INTEGER NOT NULL,
            entry_id TEXT NOT NULL,
            discovered BOOLEAN DEFAULT 1,
            discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (character_id, entry_id),
            FOREIGN KEY (character_id) REFERENCES characters (id)
        )
    ''')
    
    conn.commit()
    conn.close()
    
    # Create default owner if not exists
    create_default_owner()

def create_default_owner():
    """Create default admin/owner account"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if any owner exists
    cursor.execute("SELECT * FROM users WHERE role = 'owner' OR is_owner = 1")
    owner = cursor.fetchone()
    
    if not owner:
        # Create default owner
        username = os.getenv"username"
        password = os.getenv"password"
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        
        cursor.execute('''
            INSERT INTO users (username, password_hash, role, is_owner)
            VALUES (?, ?, ?, ?)
        ''', (username, password_hash, 'owner', 1))
        
        user_id = cursor.lastrowid
        
        # Create owner character
        cursor.execute('''
            INSERT INTO characters (user_id, name, class_name, level, gold, hp, max_hp, mana, max_mana,
                                   strength, agility, intelligence, vitality, role)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, "Admin", "warrior", 999, 999999, 99999, 99999, 99999, 99999,
              999, 999, 999, 999, 'owner'))
        
        conn.commit()
        print("✅ Default owner account created (Admin/admin123)")
    
    conn.close()

# Initialize database
init_database()

# ==================== Enums ====================

class GameClass(str, Enum):
    WARRIOR = "warrior"
    MAGE = "mage"
    ROGUE = "rogue"
    ARCHER = "archer"
    PALADIN = "paladin"
    NECROMANCER = "necromancer"

class ItemRarity(str, Enum):
    COMMON = "common"
    UNCOMMON = "uncommon"
    RARE = "rare"
    EPIC = "epic"
    LEGENDARY = "legendary"

class UserRole(str, Enum):
    PLAYER = "player"
    MODERATOR = "moderator"
    ADMIN = "admin"
    OWNER = "owner"

class QuestStatus(str, Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"

class ChatChannel(str, Enum):
    GLOBAL = "global"
    GUILD = "guild"
    PARTY = "party"
    TRADE = "trade"
    SYSTEM = "system"
    WHISPER = "whisper"
    MOD = "mod"
    ADMIN = "admin"

class TradeStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

# ==================== Pydantic Models ====================

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=20)
    password: str = Field(..., min_length=6)
    email: Optional[str] = None

class UserLogin(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    role: str
    is_owner: bool

class CharacterCreate(BaseModel):
    name: str = Field(..., min_length=3, max_length=20)
    class_name: GameClass

class CharacterResponse(BaseModel):
    id: int
    name: str
    class_name: GameClass
    level: int
    exp: int
    gold: int
    hp: int
    max_hp: int
    mana: int
    max_mana: int
    strength: int
    agility: int
    intelligence: int
    vitality: int
    role: str
    skill_points: int

class ChatMessage(BaseModel):
    channel: ChatChannel
    content: str
    target: Optional[str] = None

class TradeOffer(BaseModel):
    item_id: int
    quantity: int
    gold: int

class AuctionListing(BaseModel):
    item_id: int
    quantity: int
    starting_bid: int
    buyout_price: Optional[int]
    duration_hours: int = 24

class GuildCreate(BaseModel):
    name: str = Field(..., min_length=3, max_length=30)
    tag: str = Field(..., min_length=2, max_length=5)

# ==================== Cheat/Mod Models ====================

class CheatCommand(BaseModel):
    command: str
    target: Optional[str] = None
    amount: Optional[int] = None
    item_id: Optional[str] = None

class ModAction(BaseModel):
    action: str
    target_user_id: int
    target_character_id: Optional[int] = None
    reason: str
    duration: Optional[int] = None

class GameEvent(BaseModel):
    event_type: str
    data: Dict[str, Any]

# ==================== Game Data ====================

# Item Database
ITEMS = {
    # Weapons
    "iron_sword": {"name": "Iron Sword", "icon": "🗡️", "type": "weapon", "rarity": "common", 
                   "stats": {"attack": 12}, "value": 50, "description": "A sturdy iron sword."},
    "steel_sword": {"name": "Steel Sword", "icon": "⚔️", "type": "weapon", "rarity": "uncommon", 
                    "stats": {"attack": 22}, "value": 150, "description": "Forged from fine steel."},
    "oak_staff": {"name": "Oak Staff", "icon": "🪄", "type": "weapon", "rarity": "common", 
                  "stats": {"intelligence": 15}, "value": 45, "description": "A staff carved from ancient oak."},
    "longbow": {"name": "Longbow", "icon": "🏹", "type": "weapon", "rarity": "common", 
                "stats": {"agility": 18}, "value": 60, "description": "A reliable longbow."},
    "shadow_dagger": {"name": "Shadow Dagger", "icon": "🗡️", "type": "weapon", "rarity": "rare", 
                      "stats": {"agility": 25, "critical": 0.1}, "value": 500, "description": "Forged in shadow."},
    "dragon_blade": {"name": "Dragon Blade", "icon": "⚔️", "type": "weapon", "rarity": "epic", 
                     "stats": {"strength": 40, "fire_damage": 15}, "value": 2000, "description": "Wreathed in flame."},
    "void_orb": {"name": "Void Orb", "icon": "🔮", "type": "weapon", "rarity": "legendary", 
                 "stats": {"intelligence": 60, "void_damage": 30}, "value": 5000, "description": "An orb of incomprehensible power."},
    
    # Armor
    "leather_armor": {"name": "Leather Armor", "icon": "🧥", "type": "armor", "rarity": "common", 
                      "stats": {"defense": 10}, "value": 40, "description": "Light leather protection."},
    "chain_mail": {"name": "Chain Mail", "icon": "🛡️", "type": "armor", "rarity": "uncommon", 
                   "stats": {"defense": 25}, "value": 120, "description": "Interlocked metal rings."},
    "knight_helm": {"name": "Knight's Helm", "icon": "⛑️", "type": "armor", "rarity": "uncommon", 
                    "stats": {"defense": 15, "strength": 5}, "value": 180, "description": "The helm of a noble knight."},
    "shadow_cape": {"name": "Shadow Cape", "icon": "🌑", "type": "armor", "rarity": "epic", 
                    "stats": {"agility": 20, "critical": 0.1}, "value": 800, "description": "Woven from shadows themselves."},
    "dragon_plate": {"name": "Dragon Plate", "icon": "🛡️", "type": "armor", "rarity": "legendary", 
                     "stats": {"defense": 80, "fire_resist": 50}, "value": 3000, "description": "Made from dragon scales."},
    
    # Accessories
    "iron_ring": {"name": "Iron Ring", "icon": "💍", "type": "accessory", "rarity": "common", 
                  "stats": {"vitality": 5}, "value": 30, "description": "A simple iron ring."},
    "magic_ring": {"name": "Ring of Power", "icon": "💍", "type": "accessory", "rarity": "rare", 
                   "stats": {"all_stats": 8}, "value": 600, "description": "Radiates ancient magic."},
    "ancient_amulet": {"name": "Ancient Amulet", "icon": "📿", "type": "accessory", "rarity": "epic", 
                       "stats": {"intelligence": 30, "mana_regen": 5}, "value": 1200, "description": "Pulsing with energy."},
    
    # Consumables
    "health_potion": {"name": "Health Potion", "icon": "🧪", "type": "consumable", "rarity": "common", 
                      "effect": {"heal": 50}, "value": 25, "description": "Restores 50 HP."},
    "mana_potion": {"name": "Mana Potion", "icon": "💧", "type": "consumable", "rarity": "common", 
                    "effect": {"restore_mana": 30}, "value": 25, "description": "Restores 30 MP."},
    "elixir": {"name": "Elixir", "icon": "🧪", "type": "consumable", "rarity": "rare", 
               "effect": {"heal": 200, "restore_mana": 100}, "value": 200, "description": "Fully restores HP and MP."},
    
    # Crafting
    "iron_ore": {"name": "Iron Ore", "icon": "⛏️", "type": "crafting", "rarity": "common", 
                 "value": 10, "description": "Can be smelted into iron."},
    "magic_dust": {"name": "Magic Dust", "icon": "✨", "type": "crafting", "rarity": "uncommon", 
                   "value": 50, "description": "Infused with magical essence."},
    "dragon_scale": {"name": "Dragon Scale", "icon": "🐉", "type": "crafting", "rarity": "legendary", 
                     "value": 1000, "description": "A scale from a dragon."},
}

# Enemy Database
ENEMIES = {
    # Act 1 Enemies
    "goblin": {
        "name": "Goblin", "icon": "👺", "level": 1, "hp": 50, "max_hp": 50,
        "attack": 8, "defense": 2, "exp_reward": 25, "gold_reward": 10,
        "loot_table": [{"item_id": "iron_ore", "chance": 0.3}]
    },
    "goblin_warrior": {
        "name": "Goblin Warrior", "icon": "👹", "level": 2, "hp": 80, "max_hp": 80,
        "attack": 12, "defense": 5, "exp_reward": 40, "gold_reward": 20,
        "loot_table": [{"item_id": "iron_ore", "chance": 0.5}, {"item_id": "health_potion", "chance": 0.2}]
    },
    "goblin_chief": {
        "name": "Goblin Chief", "icon": "👑", "level": 3, "hp": 150, "max_hp": 150,
        "attack": 18, "defense": 8, "exp_reward": 100, "gold_reward": 50,
        "loot_table": [{"item_id": "iron_sword", "chance": 0.3}, {"item_id": "health_potion", "chance": 0.5}],
        "is_boss": True
    },
    
    # Act 2 Enemies
    "enemy_soldier": {
        "name": "Enemy Soldier", "icon": "⚔️", "level": 4, "hp": 120, "max_hp": 120,
        "attack": 15, "defense": 8, "exp_reward": 60, "gold_reward": 30,
        "loot_table": [{"item_id": "iron_ore", "chance": 0.4}, {"item_id": "health_potion", "chance": 0.3}]
    },
    "enemy_knight": {
        "name": "Enemy Knight", "icon": "🛡️", "level": 5, "hp": 180, "max_hp": 180,
        "attack": 22, "defense": 15, "exp_reward": 90, "gold_reward": 50,
        "loot_table": [{"item_id": "chain_mail", "chance": 0.2}, {"item_id": "health_potion", "chance": 0.4}]
    },
    "enemy_general": {
        "name": "Enemy General", "icon": "🎖️", "level": 6, "hp": 250, "max_hp": 250,
        "attack": 30, "defense": 20, "exp_reward": 150, "gold_reward": 100,
        "loot_table": [{"item_id": "steel_sword", "chance": 0.3}, {"item_id": "knight_helm", "chance": 0.3}],
        "is_boss": True
    },
    
    # Act 3 Enemies
    "corrupted_creature": {
        "name": "Corrupted Creature", "icon": "🦇", "level": 7, "hp": 200, "max_hp": 200,
        "attack": 25, "defense": 10, "exp_reward": 80, "gold_reward": 40,
        "loot_table": [{"item_id": "magic_dust", "chance": 0.4}]
    },
    "corrupted_beast": {
        "name": "Corrupted Beast", "icon": "🐗", "level": 8, "hp": 280, "max_hp": 280,
        "attack": 32, "defense": 15, "exp_reward": 110, "gold_reward": 60,
        "loot_table": [{"item_id": "magic_dust", "chance": 0.6}, {"item_id": "shadow_dagger", "chance": 0.1}]
    },
    "the_corruptor": {
        "name": "The Corruptor", "icon": "💀", "level": 10, "hp": 500, "max_hp": 500,
        "attack": 40, "defense": 20, "exp_reward": 500, "gold_reward": 300,
        "loot_table": [{"item_id": "shadow_cape", "chance": 0.5}, {"item_id": "magic_ring", "chance": 0.5}],
        "is_boss": True
    },
    
    # Act 4 Enemies
    "time_wraith": {
        "name": "Time Wraith", "icon": "👻", "level": 12, "hp": 350, "max_hp": 350,
        "attack": 35, "defense": 18, "exp_reward": 150, "gold_reward": 80,
        "loot_table": [{"item_id": "magic_dust", "chance": 0.7}]
    },
    "temporal_beast": {
        "name": "Temporal Beast", "icon": "⏳", "level": 14, "hp": 500, "max_hp": 500,
        "attack": 45, "defense": 25, "exp_reward": 200, "gold_reward": 120,
        "loot_table": [{"item_id": "ancient_amulet", "chance": 0.2}]
    },
    "time_keeper": {
        "name": "Time Keeper", "icon": "⌛", "level": 15, "hp": 1000, "max_hp": 1000,
        "attack": 50, "defense": 30, "exp_reward": 800, "gold_reward": 600,
        "loot_table": [{"item_id": "ancient_amulet", "chance": 1.0}, {"item_id": "elixir", "chance": 0.5}],
        "is_boss": True
    },
    
    # Act 5 Enemies
    "void_guardian": {
        "name": "Void Guardian", "icon": "🌑", "level": 18, "hp": 800, "max_hp": 800,
        "attack": 55, "defense": 35, "exp_reward": 400, "gold_reward": 250,
        "loot_table": [{"item_id": "dragon_scale", "chance": 0.3}]
    },
    "void_lord": {
        "name": "Void Lord", "icon": "🌌", "level": 20, "hp": 2000, "max_hp": 2000,
        "attack": 70, "defense": 40, "exp_reward": 2000, "gold_reward": 1000,
        "loot_table": [{"item_id": "void_orb", "chance": 1.0}, {"item_id": "dragon_plate", "chance": 0.5}],
        "is_boss": True,
        "is_final_boss": True
    }
}

# Quest Database - 5 Act Story with Multiple Endings
QUESTS = {
    # Act 1: Village Invasion
    "q1_1": {
        "id": "q1_1", "title": "The Dark Omen", "act": 1, "chapter": 1,
        "description": "Strange creatures have been seen near the village. Investigate the forest.",
        "dialogue": {
            "start": "Elder: 'Strange howls have been coming from the forest at night. Please investigate.'",
            "progress": "Goblin Scout: 'You shouldn't be here, human!'",
            "complete": "Elder: 'Thank you for driving them back. But this is just the beginning...'"
        },
        "objectives": [{"type": "kill", "target": "goblin", "amount": 5}],
        "rewards": {"exp": 100, "gold": 50, "items": ["health_potion"]},
        "next_quest": "q1_2",
        "choices": [
            {"id": "help", "text": "I'll help the village", "reputation": {"village": 10}},
            {"id": "demand_payment", "text": "I want payment first", "reputation": {"village": -5}}
        ]
    },
    "q1_2": {
        "id": "q1_2", "title": "The Goblin Camp", "act": 1, "chapter": 2,
        "description": "Find the goblin camp and discover who's leading them.",
        "dialogue": {
            "start": "Elder: 'We've tracked them to a camp in the hills. Find out what they're planning.'",
            "progress": "Goblin Chief: 'You'll never stop our invasion!'",
            "complete": "Elder: 'A goblin chief? This is more serious than we thought.'"
        },
        "objectives": [{"type": "explore", "target": "goblin_camp"}],
        "rewards": {"exp": 150, "gold": 75, "items": ["iron_ring"]},
        "next_quest": "q1_3"
    },
    "q1_3": {
        "id": "q1_3", "title": "Goblin Chief", "act": 1, "chapter": 3,
        "description": "Defeat the Goblin Chief leading the invasion.",
        "dialogue": {
            "start": "Elder: 'The chief must be defeated if we're to save the village.'",
            "progress": "Goblin Chief: 'Feel my wrath!'",
            "complete": "Elder: 'You've done it! The village is safe... for now.'"
        },
        "objectives": [{"type": "kill", "target": "goblin_chief", "amount": 1}],
        "rewards": {"exp": 300, "gold": 150, "items": ["iron_sword"]},
        "next_quest": "q2_1",
        "is_boss": True
    },
    
    # Act 2: Kingdom War
    "q2_1": {
        "id": "q2_1", "title": "The King's Summons", "act": 2, "chapter": 1,
        "description": "The king has summoned all able warriors. Report to the capital.",
        "dialogue": {
            "start": "Messenger: 'By order of the King, all able-bodied warriors must report to the capital immediately!'",
            "progress": "Guard: 'The king awaits. Follow me.'",
            "complete": "King: 'Our kingdom is at war. We need heroes like you.'"
        },
        "objectives": [{"type": "talk", "target": "king"}],
        "rewards": {"exp": 250, "gold": 200, "items": ["health_potion"]},
        "next_quest": "q2_2"
    },
    "q2_2": {
        "id": "q2_2", "title": "Border Skirmish", "act": 2, "chapter": 2,
        "description": "The northern border is under attack. Defend it.",
        "dialogue": {
            "start": "Captain: 'The enemy has breached our northern defenses! We need reinforcements!'",
            "progress": "Enemy Soldier: 'For the invasion!'",
            "complete": "Captain: 'You held the line! But this was just a scouting party.'"
        },
        "objectives": [{"type": "kill", "target": "enemy_soldier", "amount": 10}],
        "rewards": {"exp": 350, "gold": 250, "items": ["leather_armor"]},
        "next_quest": "q2_3"
    },
    "q2_3": {
        "id": "q2_3", "title": "Siege of the Capital", "act": 2, "chapter": 3,
        "description": "The enemy army has reached the capital. Defend the city!",
        "dialogue": {
            "start": "King: 'The enemy is at our gates! This is our darkest hour.'",
            "progress": "Enemy General: 'The capital will fall!'",
            "complete": "King: 'You've saved the kingdom! You are a true hero.'"
        },
        "objectives": [{"type": "kill", "target": "enemy_general", "amount": 1}],
        "rewards": {"exp": 600, "gold": 500, "items": ["steel_sword"]},
        "next_quest": "q3_1",
        "is_boss": True
    },
    
    # Act 3: Corruption Spreads
    "q3_1": {
        "id": "q3_1", "title": "The Plague", "act": 3, "chapter": 1,
        "description": "A strange plague is spreading across the land. Find the source.",
        "dialogue": {
            "start": "Healer: 'People are falling ill with a mysterious plague. It's like nothing I've seen.'",
            "progress": "Villager: 'The forest... it's... changing.'",
            "complete": "Healer: 'The forest is corrupted? This is worse than I feared.'"
        },
        "objectives": [{"type": "investigate", "target": "plague_origin"}],
        "rewards": {"exp": 500, "gold": 400, "items": ["health_potion"]},
        "next_quest": "q3_2"
    },
    "q3_2": {
        "id": "q3_2", "title": "Corrupted Forest", "act": 3, "chapter": 2,
        "description": "The ancient forest is being corrupted. Purify it.",
        "dialogue": {
            "start": "Druid: 'The corruption spreads through the forest. We must cleanse it.'",
            "progress": "Corrupted Creature: '*growls*'",
            "complete": "Druid: 'You've pushed back the corruption, but its source remains.'"
        },
        "objectives": [{"type": "kill", "target": "corrupted_creature", "amount": 15}],
        "rewards": {"exp": 550, "gold": 450, "items": ["magic_ring"]},
        "next_quest": "q3_3"
    },
    "q3_3": {
        "id": "q3_3", "title": "The Corruptor", "act": 3, "chapter": 3,
        "description": "Defeat the Corruptor, a powerful being spreading the corruption.",
        "dialogue": {
            "start": "Druid: 'The Corruptor awaits at the heart of the forest. Be careful.'",
            "progress": "The Corruptor: 'You cannot stop the void's influence!'",
            "complete": "Druid: 'The corruption recedes. You've saved the forest.'"
        },
        "objectives": [{"type": "kill", "target": "the_corruptor", "amount": 1}],
        "rewards": {"exp": 800, "gold": 600, "items": ["shadow_cape"]},
        "next_quest": "q4_1",
        "is_boss": True
    },
    
    # Act 4: Time Fracture
    "q4_1": {
        "id": "q4_1", "title": "Temporal Distortion", "act": 4, "chapter": 1,
        "description": "Time is breaking apart. Investigate the temporal anomalies.",
        "dialogue": {
            "start": "Wizard: 'The fabric of time is unraveling! You must investigate the rifts.'",
            "progress": "Time Wraith: 'Time... fractured...'",
            "complete": "Wizard: 'A time rift? This is beyond anything I've seen.'"
        },
        "objectives": [{"type": "explore", "target": "time_rift"}],
        "rewards": {"exp": 700, "gold": 600, "items": ["elixir"]},
        "next_quest": "q4_2"
    },
    "q4_2": {
        "id": "q4_2", "title": "Past Sins", "act": 4, "chapter": 2,
        "description": "You're thrown into the past. Witness the original corruption.",
        "dialogue": {
            "start": "Ancient Sage: 'You should not be here, traveler of time.'",
            "progress": "Sage: 'Witness the truth of what happened.'",
            "complete": "Sage: 'Now you know. The corruption was caused by... a betrayal.'"
        },
        "objectives": [{"type": "survive", "amount": 1}],
        "rewards": {"exp": 750, "gold": 650, "items": ["ancient_amulet"]},
        "next_quest": "q4_3"
    },
    "q4_3": {
        "id": "q4_3", "title": "Time Keeper", "act": 4, "chapter": 3,
        "description": "Defeat the Time Keeper to stabilize the timeline.",
        "dialogue": {
            "start": "Wizard: 'The Time Keeper guards the rift. Defeat it to return home.'",
            "progress": "Time Keeper: 'You cannot escape time!'",
            "complete": "Wizard: 'The timeline is stable. You're a true legend.'"
        },
        "objectives": [{"type": "kill", "target": "time_keeper", "amount": 1}],
        "rewards": {"exp": 1000, "gold": 800, "items": ["magic_ring"]},
        "next_quest": "q5_1",
        "is_boss": True
    },
    
    # Act 5: Multiple Endings
    "q5_1": {
        "id": "q5_1", "title": "The Final Confrontation", "act": 5, "chapter": 1,
        "description": "Face the source of all evil - The Void Lord.",
        "dialogue": {
            "start": "Wizard: 'The Void Lord awaits. This is the final battle.'",
            "progress": "Void Lord: 'You cannot stop the void!'",
            "complete": "Void Lord: 'Make your choice...'"
        },
        "objectives": [{"type": "kill", "target": "void_lord", "amount": 1}],
        "rewards": {"exp": 2000, "gold": 2000, "items": ["void_orb"]},
        "is_boss": True,
        "is_final": True,
        "choices": [
            {
                "id": "ending_hero",
                "text": "Sacrifice yourself to destroy the Void Lord",
                "reputation": {"world": 100},
                "ending": "The Hero's Sacrifice - You become a legend, remembered for eternity.",
                "companion_effect": "sadness"
            },
            {
                "id": "ending_dark",
                "text": "Embrace the void and become the new Void Lord",
                "reputation": {"world": -100},
                "ending": "The Dark Ascension - You rule the void, feared by all.",
                "companion_effect": "betrayal"
            },
            {
                "id": "ending_balance",
                "text": "Balance light and dark, seal the void away",
                "reputation": {"world": 50},
                "ending": "The Balance - Peace is restored, but at a great cost.",
                "companion_effect": "acceptance"
            },
            {
                "id": "ending_companion",
                "text": "Use your companion's power to seal the void together",
                "requires_companion": True,
                "reputation": {"companions": 100},
                "ending": "United We Stand - You and your companion become legendary heroes.",
                "companion_effect": "heroic"
            },
            {
                "id": "ending_rebirth",
                "text": "Rebirth the world anew, free from corruption",
                "requires": "ancient_amulet",
                "reputation": {"world": 50, "ancients": 100},
                "ending": "A New Dawn - The world is reborn, cleansed of all corruption.",
                "companion_effect": "peaceful"
            }
        ]
    }
}

# Codex Entries
CODEX = {
    "goblin": {"name": "Goblins", "icon": "👺", "category": "creatures",
               "lore": "Goblins are small, cunning creatures that often raid villages. They're not particularly strong but travel in packs."},
    "void": {"name": "The Void", "icon": "🌌", "category": "lore",
             "lore": "An ancient force of nothingness that seeks to consume all existence. Its origin is unknown."},
    "ancient_ones": {"name": "The Ancient Ones", "icon": "🏛️", "category": "lore",
                     "lore": "Powerful beings who shaped the world. They vanished long ago, leaving behind powerful artifacts."},
    "time_keepers": {"name": "Time Keepers", "icon": "⌛", "category": "beings",
                     "lore": "Guardians of the timeline. They ensure the flow of time remains stable."},
    "corruption": {"name": "The Corruption", "icon": "💀", "category": "phenomena",
                   "lore": "A spreading blight that twists creatures and land alike. Connected to the void."}
}

# Companions
COMPANIONS = {
    "lyra": {"name": "Lyra", "class": "mage", "icon": "🧙", 
             "description": "A young mage with immense potential. She seeks to understand the corruption."},
    "dorn": {"name": "Dorn", "class": "warrior", "icon": "⚔️",
             "description": "A seasoned warrior from the northern kingdoms. He fights for honor."},
    "sylvie": {"name": "Sylvie", "class": "rogue", "icon": "🗡️",
               "description": "A mysterious rogue with a troubled past. She knows the shadows."},
    "kael": {"name": "Kael", "class": "paladin", "icon": "🛡️",
             "description": "A holy knight devoted to protecting the innocent."}
}

# ==================== Game Classes ====================

@dataclass
class Player:
    id: int
    user_id: int
    name: str
    class_name: GameClass
    level: int = 1
    exp: int = 0
    gold: int = 100
    hp: int = 100
    max_hp: int = 100
    mana: int = 50
    max_mana: int = 50
    strength: int = 10
    agility: int = 10
    intelligence: int = 10
    vitality: int = 10
    x: int = 50
    y: int = 50
    map_id: str = "start_village"
    guild_id: Optional[int] = None
    party_id: Optional[int] = None
    role: str = "player"
    skill_points: int = 0
    status_effects: List[Dict] = field(default_factory=list)
    cooldowns: Dict[str, float] = field(default_factory=dict)
    skills: List[str] = field(default_factory=list)
    inventory: List[Dict] = field(default_factory=list)
    
    @property
    def attack_power(self):
        if self.class_name in [GameClass.WARRIOR, GameClass.PALADIN]:
            return self.strength * 2 + self.level
        elif self.class_name == GameClass.ARCHER:
            return self.agility * 2 + self.level
        elif self.class_name in [GameClass.MAGE, GameClass.NECROMANCER]:
            return self.intelligence * 2 + self.level
        else:  # ROGUE
            return self.strength + self.agility * 1.5 + self.level
    
    @property
    def defense(self):
        return self.vitality * 1.5 + self.level // 2
    
    @property
    def crit_chance(self):
        return self.agility / 200
    
    @property
    def dodge_chance(self):
        return self.agility / 300
    
    def take_damage(self, damage: int) -> int:
        # Check dodge
        if random.random() < self.dodge_chance:
            return 0
        
        reduced_damage = max(1, damage - int(self.defense / 2))
        self.hp = max(0, self.hp - reduced_damage)
        return reduced_damage
    
    def heal(self, amount: int):
        self.hp = min(self.max_hp, self.hp + amount)
    
    def restore_mana(self, amount: int):
        self.mana = min(self.max_mana, self.mana + amount)
    
    def gain_exp(self, amount: int) -> bool:
        self.exp += amount
        exp_needed = self.level * 100
        if self.exp >= exp_needed:
            self.level_up()
            return True
        return False
    
    def level_up(self):
        self.level += 1
        self.exp = 0
        self.skill_points += 2
        
        # Class-based stat increases
        if self.class_name == GameClass.WARRIOR:
            self.max_hp += 30
            self.strength += 3
            self.vitality += 3
            self.agility += 1
            self.intelligence += 1
        elif self.class_name == GameClass.MAGE:
            self.max_mana += 25
            self.intelligence += 4
            self.vitality += 1
            self.agility += 1
            self.strength += 1
        elif self.class_name == GameClass.ROGUE:
            self.agility += 4
            self.strength += 2
            self.vitality += 2
            self.intelligence += 1
        elif self.class_name == GameClass.ARCHER:
            self.agility += 4
            self.strength += 2
            self.vitality += 2
            self.intelligence += 1
        elif self.class_name == GameClass.PALADIN:
            self.max_hp += 25
            self.strength += 2
            self.vitality += 3
            self.intelligence += 2
        elif self.class_name == GameClass.NECROMANCER:
            self.max_mana += 25
            self.intelligence += 3
            self.vitality += 2
            self.strength += 1
            self.agility += 1
        
        self.hp = self.max_hp
        self.mana = self.max_mana
    
    def use_skill(self, skill_id: str, target) -> Dict:
        # Check cooldown
        now = time.time()
        if skill_id in self.cooldowns and self.cooldowns[skill_id] > now:
            return {"success": False, "message": "Skill on cooldown"}
        
        # Base skill effects (simplified)
        base_damage = self.attack_power
        mana_cost = 10
        
        if self.mana < mana_cost:
            return {"success": False, "message": "Not enough mana"}
        
        self.mana -= mana_cost
        
        # Critical hit
        is_critical = random.random() < self.crit_chance
        if is_critical:
            base_damage = int(base_damage * 1.8)
        
        # Apply skill modifiers
        if skill_id == "power_strike":
            damage = int(base_damage * 1.5)
        elif skill_id == "fireball":
            damage = int(self.intelligence * 2.5)
        elif skill_id == "backstab":
            damage = int(self.agility * 2.2)
            is_critical = True
        else:
            damage = base_damage
        
        # Set cooldown
        self.cooldowns[skill_id] = now + 3
        
        return {
            "success": True,
            "damage": damage,
            "critical": is_critical,
            "mana_cost": mana_cost
        }
    
    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "class": self.class_name.value,
            "level": self.level,
            "hp": self.hp,
            "max_hp": self.max_hp,
            "mana": self.mana,
            "max_mana": self.max_mana,
            "gold": self.gold,
            "x": self.x,
            "y": self.y,
            "map": self.map_id,
            "role": self.role
        }

@dataclass
class Enemy:
    id: str
    name: str
    level: int
    hp: int
    max_hp: int
    attack: int
    defense: int
    exp_reward: int
    gold_reward: int
    loot_table: List[Dict]
    skills: List[str] = field(default_factory=list)
    is_boss: bool = False
    
    def take_damage(self, damage: int) -> int:
        reduced_damage = max(1, damage - self.defense)
        self.hp = max(0, self.hp - reduced_damage)
        return reduced_damage
    
    def attack_target(self, player: Player) -> Dict:
        damage = max(1, self.attack - int(player.defense / 2))
        actual_damage = player.take_damage(damage)
        
        return {
            "damage": actual_damage,
            "message": f"{self.name} attacks for {actual_damage} damage"
        }

# ==================== Combat Engine ====================

class CombatEngine:
    def __init__(self):
        self.active_combats: Dict[str, Dict] = {}
    
    async def calculate_damage(self, attacker: Player, defender, skill_id: Optional[str] = None) -> Dict:
        if isinstance(defender, Player):
            # PvP damage calculation
            base_damage = attacker.attack_power
            defense = defender.defense
        else:
            # PvE damage calculation
            base_damage = attacker.attack_power
            defense = defender.defense
        
        # Critical hit
        is_critical = random.random() < attacker.crit_chance
        if is_critical:
            base_damage = int(base_damage * 1.5)
        
        # Apply damage
        actual_damage = max(1, base_damage - int(defense / 3))
        
        if isinstance(defender, Player):
            defender.hp = max(0, defender.hp - actual_damage)
        else:
            defender.hp = max(0, defender.hp - actual_damage)
        
        return {
            "damage": actual_damage,
            "critical": is_critical,
            "target_hp": defender.hp,
            "target_max_hp": defender.max_hp,
            "message": f"Dealt {actual_damage} damage{' (CRITICAL!)' if is_critical else ''}"
        }
    
    async def start_pvp_combat(self, player1: Player, player2: Player) -> Dict:
        """Simulate PvP combat between two players"""
        combat_log = []
        turn = 1
        max_turns = 50
        
        # Determine first attacker (higher agility)
        if player1.agility >= player2.agility:
            first, second = player1, player2
        else:
            first, second = player2, player1
        
        combat_log.append({
            "turn": 0,
            "message": f"Match started: {player1.name} ({player1.class_name.value}) vs {player2.name} ({player2.class_name.value})",
            "first_attacker": first.name
        })
        
        # Combat loop
        while player1.hp > 0 and player2.hp > 0 and turn <= max_turns:
            # First attacker's turn
            if player1.hp > 0 and player2.hp > 0:
                damage_result = await self.calculate_damage(first, second, None)
                combat_log.append({
                    "turn": turn,
                    "attacker": first.name,
                    "damage": damage_result["damage"],
                    "critical": damage_result["critical"],
                    "target_hp": damage_result["target_hp"],
                    "message": damage_result["message"]
                })
            
            if second.hp <= 0:
                break
            
            turn += 1
            
            # Second attacker's turn
            if player1.hp > 0 and player2.hp > 0:
                damage_result = await self.calculate_damage(second, first, None)
                combat_log.append({
                    "turn": turn,
                    "attacker": second.name,
                    "damage": damage_result["damage"],
                    "critical": damage_result["critical"],
                    "target_hp": damage_result["target_hp"],
                    "message": damage_result["message"]
                })
            
            turn += 1
        
        # Determine winner
        if player1.hp <= 0:
            winner = player2
            loser = player1
        elif player2.hp <= 0:
            winner = player1
            loser = player2
        else:
            # Draw - winner by higher HP
            if player1.hp > player2.hp:
                winner = player1
                loser = player2
            else:
                winner = player2
                loser = player1
        
        return {
            "winner": winner,
            "loser": loser,
            "combat_log": combat_log,
            "player1_final_hp": player1.hp,
            "player2_final_hp": player2.hp
        }

combat_engine = CombatEngine()

# ==================== Quest Engine ====================

class QuestEngine:
    def __init__(self):
        self.quests = QUESTS
    
    async def start_quest(self, character_id: int, quest_id: str) -> Dict:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if already started
        cursor.execute(
            "SELECT * FROM quests WHERE character_id = ? AND quest_id = ?",
            (character_id, quest_id)
        )
        existing = cursor.fetchone()
        
        if existing:
            conn.close()
            return {"success": False, "message": "Quest already started"}
        
        quest = self.quests.get(quest_id)
        if not quest:
            conn.close()
            return {"success": False, "message": "Quest not found"}
        
        # Initialize objectives as JSON
        objectives = json.dumps(quest.get("objectives", []))
        
        cursor.execute('''
            INSERT INTO quests (character_id, quest_id, status, objectives, started_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (character_id, quest_id, "in_progress", objectives, datetime.datetime.now().isoformat()))
        
        conn.commit()
        conn.close()
        
        return {"success": True, "message": f"Quest '{quest['title']}' started"}
    
    async def update_quest_progress(self, character_id: int, event_type: str, event_data: Dict) -> Dict:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get active quests
        cursor.execute(
            "SELECT * FROM quests WHERE character_id = ? AND status = 'in_progress'",
            (character_id,)
        )
        active_quests = cursor.fetchall()
        
        updates = []
        
        for q in active_quests:
            quest_id = q["quest_id"]
            quest = self.quests.get(quest_id)
            if not quest:
                continue
            
            objectives = json.loads(q["objectives"] or "[]")
            progress = q["progress"] or 0
            completed = True
            
            # Update progress based on event
            for obj in objectives:
                if obj["type"] == event_type and obj["target"] == event_data.get("target"):
                    progress += event_data.get("amount", 1)
                    if progress < obj["amount"]:
                        completed = False
            
            if completed:
                # Quest completed
                cursor.execute('''
                    UPDATE quests
                    SET status = ?, completed_at = ?
                    WHERE character_id = ? AND quest_id = ?
                ''', ("completed", datetime.datetime.now().isoformat(), character_id, quest_id))
                
                # Grant rewards
                rewards = quest.get("rewards", {})
                if rewards:
                    cursor.execute(
                        "UPDATE characters SET exp = exp + ?, gold = gold + ? WHERE id = ?",
                        (rewards.get("exp", 0), rewards.get("gold", 0), character_id)
                    )
                    
                    # Add items
                    for item in rewards.get("items", []):
                        cursor.execute('''
                            INSERT INTO inventory (character_id, item_id, quantity)
                            VALUES (?, ?, 1)
                        ''', (character_id, item))
                
                updates.append({"quest_id": quest_id, "completed": True})
            else:
                # Update progress
                cursor.execute('''
                    UPDATE quests SET progress = ? WHERE character_id = ? AND quest_id = ?
                ''', (progress, character_id, quest_id))
                
                updates.append({"quest_id": quest_id, "progress": progress})
        
        conn.commit()
        conn.close()
        
        return {"success": True, "updates": updates}

quest_engine = QuestEngine()

# ==================== Security ====================

security = HTTPBearer()

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode(), salt).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.datetime.utcnow() + datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id: int = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    
    if user["banned"]:
        raise HTTPException(status_code=403, detail="User is banned")
    
    return dict(user)

async def require_owner(user = Depends(get_current_user)):
    """Require owner role"""
    if not user.get('is_owner', False) and user.get('role') != 'owner':
        raise HTTPException(status_code=403, detail="Owner access required")
    return user

async def require_admin(user = Depends(get_current_user)):
    """Require admin or owner role"""
    role = user.get('role', 'player')
    if role not in ['admin', 'owner'] and not user.get('is_owner', False):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

async def require_mod(user = Depends(get_current_user)):
    """Require moderator, admin, or owner role"""
    role = user.get('role', 'player')
    if role not in ['moderator', 'admin', 'owner'] and not user.get('is_owner', False):
        raise HTTPException(status_code=403, detail="Moderator access required")
    return user

# ==================== WebSocket Manager ====================

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, WebSocket] = {}
        self.user_roles: Dict[int, str] = {}
        self.character_ids: Dict[int, int] = {}
        self.rooms: Dict[str, Set[int]] = {
            "global": set(),
            "trade": set(),
            "pvp": set(),
            "mod": set(),
            "admin": set()
        }
        self.banned_users: Set[int] = set()
        self.muted_users: Dict[int, datetime.datetime] = {}
        self.chat_history: List[Dict] = []
    
    async def connect(self, websocket: WebSocket, user_id: int, role: str = "player"):
        await websocket.accept()
        self.active_connections[user_id] = websocket
        self.user_roles[user_id] = role
        self.rooms["global"].add(user_id)
        
        # Get character ID for this user
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM characters WHERE user_id = ?", (user_id,))
        char = cursor.fetchone()
        if char:
            self.character_ids[user_id] = char["id"]
        conn.close()
        
        # Add to mod/admin rooms if applicable
        if role in ["moderator", "admin", "owner"]:
            self.rooms["mod"].add(user_id)
        if role in ["admin", "owner"]:
            self.rooms["admin"].add(user_id)
    
    def disconnect(self, user_id: int):
        if user_id in self.active_connections:
            del self.active_connections[user_id]
        if user_id in self.user_roles:
            del self.user_roles[user_id]
        if user_id in self.character_ids:
            del self.character_ids[user_id]
        for room in self.rooms.values():
            room.discard(user_id)
    
    async def send_personal_message(self, user_id: int, message: dict):
        if user_id in self.active_connections and user_id not in self.banned_users:
            try:
                await self.active_connections[user_id].send_json(message)
            except:
                pass
    
    async def broadcast_to_room(self, room: str, message: dict, exclude_user: int = None):
        if room in self.rooms:
            for user_id in self.rooms[room]:
                if exclude_user and user_id == exclude_user:
                    continue
                await self.send_personal_message(user_id, message)
    
    async def handle_chat_message(self, user_id: int, message: ChatMessage):
        # Check if user is muted
        if user_id in self.muted_users:
            mute_until = self.muted_users[user_id]
            if datetime.datetime.now() < mute_until:
                await self.send_personal_message(user_id, {
                    "type": "error",
                    "message": f"You are muted until {mute_until.strftime('%H:%M:%S')}"
                })
                return
            else:
                del self.muted_users[user_id]
        
        # Get character name
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name, class_name FROM characters WHERE user_id = ?", (user_id,))
        char = cursor.fetchone()
        conn.close()
        
        character_name = char["name"] if char else f"User_{user_id}"
        role = self.user_roles.get(user_id, "player")
        
        # Add role tag
        role_tag = ""
        if role == "owner":
            role_tag = "👑 "
        elif role == "admin":
            role_tag = "⚡ "
        elif role == "moderator":
            role_tag = "🛡️ "
        
        chat_entry = {
            "type": "chat",
            "channel": message.channel,
            "sender": f"{role_tag}{character_name}",
            "sender_id": user_id,
            "role": role,
            "content": message.content,
            "timestamp": datetime.datetime.now().isoformat()
        }
        
        # Store in history
        self.chat_history.append(chat_entry)
        if len(self.chat_history) > 100:
            self.chat_history.pop(0)
        
        # Handle whisper
        if message.channel == ChatChannel.WHISPER and message.target:
            # Find target user by character name
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT user_id FROM characters WHERE name = ?
            ''', (message.target,))
            target = cursor.fetchone()
            conn.close()
            
            if target:
                await self.send_personal_message(target["user_id"], chat_entry)
                await self.send_personal_message(user_id, chat_entry)
            else:
                await self.send_personal_message(user_id, {
                    "type": "error",
                    "message": f"Player '{message.target}' not found"
                })
        elif message.channel == ChatChannel.MOD:
            await self.broadcast_to_room("mod", chat_entry)
        elif message.channel == ChatChannel.ADMIN:
            await self.broadcast_to_room("admin", chat_entry)
        else:
            await self.broadcast_to_room("global", chat_entry)

manager = ConnectionManager()

# ==================== FastAPI App ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("=" * 60)
    print("🚀 Starting Pixel RPG Server")
    print("=" * 60)
    print("✅ Database initialized")
    print("✅ Server ready")
    yield
    # Shutdown
    print("🛑 Server shutting down")

app = FastAPI(lifespan=lifespan)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== Auth Routes ====================

@app.post("/api/register", response_model=TokenResponse)
async def register(user: UserCreate):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if user exists
    cursor.execute("SELECT * FROM users WHERE username = ?", (user.username,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Username already exists")
    
    # Check if email exists
    if user.email:
        cursor.execute("SELECT * FROM users WHERE email = ?", (user.email,))
        if cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail="Email already registered")
    
    # Create user
    password_hash = hash_password(user.password)
    
    cursor.execute('''
        INSERT INTO users (username, password_hash, email, role, is_owner, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user.username, password_hash, user.email, 'player', 0, datetime.datetime.now().isoformat()))
    
    user_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    token = create_access_token({"sub": str(user_id), "role": "player", "is_owner": False})
    
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": "player",
        "is_owner": False
    }

@app.post("/api/login", response_model=TokenResponse)
async def login(user: UserLogin):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM users WHERE username = ?", (user.username,))
    db_user = cursor.fetchone()
    conn.close()
    
    if not db_user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    if not verify_password(user.password, db_user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    if db_user["banned"]:
        raise HTTPException(status_code=403, detail="Account is banned")
    
    # Update last login
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET last_login = ? WHERE id = ?", 
                   (datetime.datetime.now().isoformat(), db_user["id"]))
    conn.commit()
    conn.close()
    
    token = create_access_token({
        "sub": str(db_user["id"]),
        "role": db_user.get('role', 'player'),
        "is_owner": bool(db_user.get('is_owner', False))
    })
    
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": db_user.get('role', 'player'),
        "is_owner": bool(db_user.get('is_owner', False))
    }

# ==================== Character Routes ====================

@app.post("/api/characters", response_model=CharacterResponse)
async def create_character(character: CharacterCreate, user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if character name exists
    cursor.execute("SELECT * FROM characters WHERE name = ?", (character.name,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Character name already exists")
    
    # Check if user already has max characters (3)
    cursor.execute("SELECT COUNT(*) as count FROM characters WHERE user_id = ?", (user["id"],))
    count_row = cursor.fetchone()
    count = count_row["count"] if count_row else 0
    if count >= 3:
        conn.close()
        raise HTTPException(status_code=400, detail="Maximum characters reached (3)")
    
    # Base stats per class
    base_stats = {
        GameClass.WARRIOR: {"hp": 120, "mana": 40, "strength": 15, "agility": 8, "intelligence": 5, "vitality": 14},
        GameClass.MAGE: {"hp": 80, "mana": 100, "strength": 5, "agility": 7, "intelligence": 18, "vitality": 8},
        GameClass.ROGUE: {"hp": 90, "mana": 60, "strength": 10, "agility": 18, "intelligence": 7, "vitality": 10},
        GameClass.ARCHER: {"hp": 95, "mana": 55, "strength": 8, "agility": 17, "intelligence": 8, "vitality": 11},
        GameClass.PALADIN: {"hp": 110, "mana": 60, "strength": 12, "agility": 6, "intelligence": 10, "vitality": 15},
        GameClass.NECROMANCER: {"hp": 85, "mana": 110, "strength": 6, "agility": 6, "intelligence": 17, "vitality": 9}
    }
    
    stats = base_stats[character.class_name]
    role = user.get('role', 'player')
    
    # Starting gold
    starting_gold = 100
    if role == "owner" or user.get('is_owner', False):
        starting_gold = 100000
    
    cursor.execute('''
        INSERT INTO characters (
            user_id, name, class_name, hp, max_hp, mana, max_mana,
            strength, agility, intelligence, vitality, gold, role
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        user["id"], character.name, character.class_name.value,
        stats["hp"], stats["hp"], stats["mana"], stats["mana"],
        stats["strength"], stats["agility"], stats["intelligence"], stats["vitality"],
        starting_gold, role
    ))
    
    character_id = cursor.lastrowid
    conn.commit()
    
    # Get created character
    cursor.execute("SELECT * FROM characters WHERE id = ?", (character_id,))
    char_data = cursor.fetchone()
    conn.close()
    
    # Convert to dictionary for safe access
    char_dict = dict(char_data)
    
    return CharacterResponse(
        id=char_dict["id"],
        name=char_dict["name"],
        class_name=character.class_name,
        level=char_dict["level"],
        exp=char_dict["exp"],
        gold=char_dict["gold"],
        hp=char_dict["hp"],
        max_hp=char_dict["max_hp"],
        mana=char_dict["mana"],
        max_mana=char_dict["max_mana"],
        strength=char_dict["strength"],
        agility=char_dict["agility"],
        intelligence=char_dict["intelligence"],
        vitality=char_dict["vitality"],
        role=char_dict.get('role', 'player'),
        skill_points=char_dict["skill_points"]
    )

@app.get("/api/characters", response_model=List[CharacterResponse])
async def get_characters(user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM characters WHERE user_id = ?", (user["id"],))
    rows = cursor.fetchall()
    conn.close()
    
    characters = []
    for row in rows:
        # Convert to dictionary first for safe access
        row_dict = dict(row)
        characters.append(CharacterResponse(
            id=row_dict["id"],
            name=row_dict["name"],
            class_name=GameClass(row_dict["class_name"]),
            level=row_dict["level"],
            exp=row_dict["exp"],
            gold=row_dict["gold"],
            hp=row_dict["hp"],
            max_hp=row_dict["max_hp"],
            mana=row_dict["mana"],
            max_mana=row_dict["max_mana"],
            strength=row_dict["strength"],
            agility=row_dict["agility"],
            intelligence=row_dict["intelligence"],
            vitality=row_dict["vitality"],
            role=row_dict.get('role', 'player'),  # Safe get using dict
            skill_points=row_dict["skill_points"]
        ))
    
    return characters

@app.get("/api/characters", response_model=List[CharacterResponse])
async def get_characters(user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM characters WHERE user_id = ?", (user["id"],))
    rows = cursor.fetchall()
    conn.close()
    
    characters = []
    for row in rows:
        characters.append(CharacterResponse(
            id=row["id"],
            name=row["name"],
            class_name=GameClass(row["class_name"]),
            level=row["level"],
            exp=row["exp"],
            gold=row["gold"],
            hp=row["hp"],
            max_hp=row["max_hp"],
            mana=row["mana"],
            max_mana=row["max_mana"],
            strength=row["strength"],
            agility=row["agility"],
            intelligence=row["intelligence"],
            vitality=row["vitality"],
            role=row.get('role', 'player'),
            skill_points=row["skill_points"]
        ))
    
    return characters

@app.get("/api/characters/{character_id}")
async def get_character(character_id: int, user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get character
    cursor.execute('''
        SELECT * FROM characters
        WHERE id = ? AND user_id = ?
    ''', (character_id, user["id"]))
    character = cursor.fetchone()
    
    if not character:
        conn.close()
        raise HTTPException(status_code=404, detail="Character not found")
    
    # Get inventory
    cursor.execute('''
        SELECT * FROM inventory
        WHERE character_id = ?
    ''', (character_id,))
    inventory = cursor.fetchall()
    
    # Get skills
    cursor.execute('''
        SELECT * FROM skills
        WHERE character_id = ?
    ''', (character_id,))
    skills = cursor.fetchall()
    
    # Get active quests
    cursor.execute('''
        SELECT * FROM quests
        WHERE character_id = ? AND status = 'in_progress'
    ''', (character_id,))
    quests = cursor.fetchall()
    
    # Get story progression
    cursor.execute('''
        SELECT * FROM story_progression
        WHERE character_id = ?
    ''', (character_id,))
    story = cursor.fetchone()
    
    conn.close()
    
    return {
        "character": dict(character),
        "inventory": [dict(i) for i in inventory],
        "skills": [dict(s) for s in skills],
        "quests": [dict(q) for q in quests],
        "story": dict(story) if story else None
    }

# ==================== Game Routes ====================

@app.post("/api/characters/{character_id}/move")
async def move_character(character_id: int, x: int, y: int, user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE characters
        SET x_position = ?, y_position = ?
        WHERE id = ? AND user_id = ?
    ''', (x, y, character_id, user["id"]))
    
    conn.commit()
    conn.close()
    
    # Broadcast movement
    await manager.broadcast_to_room("global", {
        "type": "movement",
        "character_id": character_id,
        "x": x,
        "y": y
    }, exclude_user=user["id"])
    
    return {"success": True}

@app.post("/api/characters/{character_id}/combat/{enemy_id}")
async def start_combat(character_id: int, enemy_id: str, user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get character
    cursor.execute("SELECT * FROM characters WHERE id = ? AND user_id = ?", (character_id, user["id"]))
    char_data = cursor.fetchone()
    
    if not char_data:
        conn.close()
        raise HTTPException(status_code=404, detail="Character not found")
    
    # Create player object
    player = Player(
        id=char_data["id"],
        user_id=char_data["user_id"],
        name=char_data["name"],
        class_name=GameClass(char_data["class_name"]),
        level=char_data["level"],
        exp=char_data["exp"],
        gold=char_data["gold"],
        hp=char_data["hp"],
        max_hp=char_data["max_hp"],
        mana=char_data["mana"],
        max_mana=char_data["max_mana"],
        strength=char_data["strength"],
        agility=char_data["agility"],
        intelligence=char_data["intelligence"],
        vitality=char_data["vitality"],
        role=char_data.get('role', 'player'),
        skill_points=char_data["skill_points"]
    )
    
    # Get enemy
    enemy_data = ENEMIES.get(enemy_id)
    if not enemy_data:
        conn.close()
        raise HTTPException(status_code=404, detail="Enemy not found")
    
    enemy = Enemy(
        id=enemy_id,
        name=enemy_data["name"],
        level=enemy_data["level"],
        hp=enemy_data["hp"],
        max_hp=enemy_data["max_hp"],
        attack=enemy_data["attack"],
        defense=enemy_data["defense"],
        exp_reward=enemy_data["exp_reward"],
        gold_reward=enemy_data["gold_reward"],
        loot_table=enemy_data.get("loot_table", []),
        is_boss=enemy_data.get("is_boss", False)
    )
    
    combat_log = []
    victory = False
    
    # Combat loop
    while player.hp > 0 and enemy.hp > 0:
        # Player turn
        damage_result = await combat_engine.calculate_damage(player, enemy, None)
        combat_log.append({
            "turn": len(combat_log) + 1,
            "attacker": "player",
            **damage_result
        })
        
        if enemy.hp <= 0:
            victory = True
            break
        
        # Enemy turn
        enemy_attack = enemy.attack_target(player)
        combat_log.append({
            "turn": len(combat_log) + 1,
            "attacker": "enemy",
            "damage": enemy_attack["damage"],
            "message": enemy_attack["message"],
            "player_hp": player.hp
        })
    
    if victory:
        # Reward player
        player.gain_exp(enemy.exp_reward)
        player.gold += enemy.gold_reward
        
        # Check for loot
        loot_items = []
        for loot in enemy.loot_table:
            if random.random() < loot["chance"]:
                loot_items.append(loot["item_id"])
                cursor.execute('''
                    INSERT INTO inventory (character_id, item_id, quantity)
                    VALUES (?, ?, 1)
                ''', (character_id, loot["item_id"]))
        
        # Update character in database
        cursor.execute('''
            UPDATE characters
            SET hp = ?, mana = ?, exp = ?, gold = ?, level = ?
            WHERE id = ?
        ''', (player.hp, player.mana, player.exp, player.gold, player.level, character_id))
        
        # Update quest progress
        await quest_engine.update_quest_progress(character_id, "kill", {"target": enemy_id, "amount": 1})
    
    conn.commit()
    conn.close()
    
    return {
        "victory": victory,
        "player_hp": player.hp,
        "player_max_hp": player.max_hp,
        "enemy_hp": enemy.hp,
        "enemy_max_hp": enemy.max_hp,
        "exp_gained": enemy.exp_reward if victory else 0,
        "gold_gained": enemy.gold_reward if victory else 0,
        "loot": loot_items if victory else [],
        "combat_log": combat_log[-10:]  # Last 10 actions
    }

# ==================== Trading Routes ====================

@app.post("/api/trade/initiate/{target_character_id}")
async def initiate_trade(target_character_id: int, user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get initiator character
    cursor.execute("SELECT id FROM characters WHERE user_id = ?", (user["id"],))
    initiator = cursor.fetchone()
    
    if not initiator:
        conn.close()
        raise HTTPException(status_code=404, detail="Character not found")
    
    # Check if target exists
    cursor.execute("SELECT user_id FROM characters WHERE id = ?", (target_character_id,))
    target = cursor.fetchone()
    
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="Target character not found")
    
    # Create trade
    cursor.execute('''
        INSERT INTO trades (initiator_id, target_id, status, created_at)
        VALUES (?, ?, ?, ?)
    ''', (initiator["id"], target_character_id, "pending", datetime.datetime.now().isoformat()))
    
    trade_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    # Notify target
    await manager.send_personal_message(target["user_id"], {
        "type": "trade_request",
        "trade_id": trade_id,
        "from_character": initiator["id"]
    })
    
    return {"trade_id": trade_id}

@app.post("/api/trade/{trade_id}/accept")
async def accept_trade(trade_id: int, user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get character
    cursor.execute("SELECT id FROM characters WHERE user_id = ?", (user["id"],))
    character = cursor.fetchone()
    
    if not character:
        conn.close()
        raise HTTPException(status_code=404, detail="Character not found")
    
    # Get trade
    cursor.execute('''
        SELECT * FROM trades WHERE id = ? AND (initiator_id = ? OR target_id = ?)
    ''', (trade_id, character["id"], character["id"]))
    trade = cursor.fetchone()
    
    if not trade:
        conn.close()
        raise HTTPException(status_code=404, detail="Trade not found")
    
    # Update trade status
    if trade["status"] == "pending":
        cursor.execute('''
            UPDATE trades SET status = ? WHERE id = ?
        ''', ("accepted", trade_id))
    elif trade["status"] == "accepted":
        # Both accepted, complete trade
        # Validate and transfer items/gold
        initiator_items = json.loads(trade["initiator_items"] or "[]")
        target_items = json.loads(trade["target_items"] or "[]")
        initiator_gold = trade["initiator_gold"]
        target_gold = trade["target_gold"]
        
        # Get character inventories
        cursor.execute("SELECT * FROM inventory WHERE character_id = ?", (trade["initiator_id"],))
        initiator_inv = {i["item_id"]: i for i in cursor.fetchall()}
        
        cursor.execute("SELECT * FROM inventory WHERE character_id = ?", (trade["target_id"],))
        target_inv = {i["item_id"]: i for i in cursor.fetchall()}
        
        # Verify initiator has items
        for item in initiator_items:
            if item["item_id"] not in initiator_inv or initiator_inv[item["item_id"]]["quantity"] < item["quantity"]:
                conn.close()
                raise HTTPException(status_code=400, detail="Initiator missing items")
        
        # Verify target has items
        for item in target_items:
            if item["item_id"] not in target_inv or target_inv[item["item_id"]]["quantity"] < item["quantity"]:
                conn.close()
                raise HTTPException(status_code=400, detail="Target missing items")
        
        # Verify gold amounts
        cursor.execute("SELECT gold FROM characters WHERE id = ?", (trade["initiator_id"],))
        initiator_gold_current = cursor.fetchone()["gold"]
        
        cursor.execute("SELECT gold FROM characters WHERE id = ?", (trade["target_id"],))
        target_gold_current = cursor.fetchone()["gold"]
        
        if initiator_gold_current < initiator_gold or target_gold_current < target_gold:
            conn.close()
            raise HTTPException(status_code=400, detail="Insufficient gold")
        
        # Transfer items
        for item in initiator_items:
            # Remove from initiator
            cursor.execute('''
                UPDATE inventory SET quantity = quantity - ?
                WHERE character_id = ? AND item_id = ?
            ''', (item["quantity"], trade["initiator_id"], item["item_id"]))
            
            # Add to target
            cursor.execute('''
                INSERT INTO inventory (character_id, item_id, quantity)
                VALUES (?, ?, ?)
                ON CONFLICT(character_id, item_id) DO UPDATE SET quantity = quantity + ?
            ''', (trade["target_id"], item["item_id"], item["quantity"], item["quantity"]))
        
        for item in target_items:
            # Remove from target
            cursor.execute('''
                UPDATE inventory SET quantity = quantity - ?
                WHERE character_id = ? AND item_id = ?
            ''', (item["quantity"], trade["target_id"], item["item_id"]))
            
            # Add to initiator
            cursor.execute('''
                INSERT INTO inventory (character_id, item_id, quantity)
                VALUES (?, ?, ?)
                ON CONFLICT(character_id, item_id) DO UPDATE SET quantity = quantity + ?
            ''', (trade["initiator_id"], item["item_id"], item["quantity"], item["quantity"]))
        
        # Transfer gold
        cursor.execute('''
            UPDATE characters SET gold = gold - ? WHERE id = ?
        ''', (initiator_gold, trade["initiator_id"]))
        cursor.execute('''
            UPDATE characters SET gold = gold + ? WHERE id = ?
        ''', (initiator_gold, trade["target_id"]))
        
        cursor.execute('''
            UPDATE characters SET gold = gold - ? WHERE id = ?
        ''', (target_gold, trade["target_id"]))
        cursor.execute('''
            UPDATE characters SET gold = gold + ? WHERE id = ?
        ''', (target_gold, trade["initiator_id"]))
        
        # Complete trade
        cursor.execute('''
            UPDATE trades SET status = ?, completed_at = ? WHERE id = ?
        ''', ("completed", datetime.datetime.now().isoformat(), trade_id))
    
    conn.commit()
    conn.close()
    
    return {"success": True}

@app.post("/api/trade/{trade_id}/update")
async def update_trade(trade_id: int, items: List[Dict], gold: int, user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get character
    cursor.execute("SELECT id FROM characters WHERE user_id = ?", (user["id"],))
    character = cursor.fetchone()
    
    if not character:
        conn.close()
        raise HTTPException(status_code=404, detail="Character not found")
    
    # Get trade
    cursor.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
    trade = cursor.fetchone()
    
    if not trade:
        conn.close()
        raise HTTPException(status_code=404, detail="Trade not found")
    
    # Determine which side is updating
    if trade["initiator_id"] == character["id"]:
        cursor.execute('''
            UPDATE trades SET initiator_items = ?, initiator_gold = ? WHERE id = ?
        ''', (json.dumps(items), gold, trade_id))
    elif trade["target_id"] == character["id"]:
        cursor.execute('''
            UPDATE trades SET target_items = ?, target_gold = ? WHERE id = ?
        ''', (json.dumps(items), gold, trade_id))
    else:
        conn.close()
        raise HTTPException(status_code=403, detail="Not part of this trade")
    
    conn.commit()
    conn.close()
    
    return {"success": True}

# ==================== PvP Routes ====================

@app.post("/api/pvp/challenge/{target_character_id}")
async def challenge_pvp(target_character_id: int, user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get challenger character
    cursor.execute("SELECT id FROM characters WHERE user_id = ?", (user["id"],))
    challenger = cursor.fetchone()
    
    if not challenger:
        conn.close()
        raise HTTPException(status_code=404, detail="Character not found")
    
    # Check if target exists
    cursor.execute("SELECT user_id FROM characters WHERE id = ?", (target_character_id,))
    target = cursor.fetchone()
    
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="Target character not found")
    
    # Create PvP match
    cursor.execute('''
        INSERT INTO pvp_matches (player1_id, player2_id, status, started_at)
        VALUES (?, ?, ?, ?)
    ''', (challenger["id"], target_character_id, "pending", datetime.datetime.now().isoformat()))
    
    match_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    # Notify target
    await manager.send_personal_message(target["user_id"], {
        "type": "pvp_challenge",
        "match_id": match_id,
        "challenger": challenger["id"]
    })
    
    return {"match_id": match_id}

@app.post("/api/pvp/match/{match_id}/accept")
async def accept_pvp(match_id: int, user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get character
    cursor.execute("SELECT * FROM characters WHERE user_id = ?", (user["id"],))
    character = cursor.fetchone()
    
    if not character:
        conn.close()
        raise HTTPException(status_code=404, detail="Character not found")
    
    # Get match
    cursor.execute("SELECT * FROM pvp_matches WHERE id = ?", (match_id,))
    match = cursor.fetchone()
    
    if not match:
        conn.close()
        raise HTTPException(status_code=404, detail="Match not found")
    
    if match["status"] != "pending":
        conn.close()
        raise HTTPException(status_code=400, detail="Match already processed")
    
    # Get both players
    cursor.execute("SELECT * FROM characters WHERE id = ?", (match["player1_id"],))
    player1_data = cursor.fetchone()
    
    cursor.execute("SELECT * FROM characters WHERE id = ?", (match["player2_id"],))
    player2_data = cursor.fetchone()
    
    if not player1_data or not player2_data:
        conn.close()
        raise HTTPException(status_code=404, detail="Players not found")
    
    # Create player objects
    player1 = Player(
        id=player1_data["id"],
        user_id=player1_data["user_id"],
        name=player1_data["name"],
        class_name=GameClass(player1_data["class_name"]),
        level=player1_data["level"],
        exp=player1_data["exp"],
        gold=player1_data["gold"],
        hp=player1_data["hp"],
        max_hp=player1_data["max_hp"],
        mana=player1_data["mana"],
        max_mana=player1_data["max_mana"],
        strength=player1_data["strength"],
        agility=player1_data["agility"],
        intelligence=player1_data["intelligence"],
        vitality=player1_data["vitality"],
        role=player1_data.get('role', 'player'),
        skill_points=player1_data["skill_points"]
    )
    
    player2 = Player(
        id=player2_data["id"],
        user_id=player2_data["user_id"],
        name=player2_data["name"],
        class_name=GameClass(player2_data["class_name"]),
        level=player2_data["level"],
        exp=player2_data["exp"],
        gold=player2_data["gold"],
        hp=player2_data["hp"],
        max_hp=player2_data["max_hp"],
        mana=player2_data["mana"],
        max_mana=player2_data["max_mana"],
        strength=player2_data["strength"],
        agility=player2_data["agility"],
        intelligence=player2_data["intelligence"],
        vitality=player2_data["vitality"],
        role=player2_data.get('role', 'player'),
        skill_points=player2_data["skill_points"]
    )
    
    # Start combat
    result = await combat_engine.start_pvp_combat(player1, player2)
    winner = result["winner"]
    loser = result["loser"]
    
    # Calculate rewards
    gold_reward = 50 + (loser.level * 10)
    exp_reward = 100 + (loser.level * 20)
    
    # Update winner
    cursor.execute('''
        UPDATE characters
        SET gold = gold + ?, exp = exp + ?, hp = ?
        WHERE id = ?
    ''', (gold_reward, exp_reward, winner.hp, winner.id))
    
    # Update loser
    cursor.execute('''
        UPDATE characters
        SET hp = ?
        WHERE id = ?
    ''', (loser.hp, loser.id))
    
    # Update PvP stats
    cursor.execute('''
        INSERT INTO pvp_stats (character_id, wins, losses, rating)
        VALUES (?, 1, 0, ?)
        ON CONFLICT(character_id) DO UPDATE SET
            wins = wins + 1,
            rating = rating + 25
    ''', (winner.id, 1025))
    
    cursor.execute('''
        INSERT INTO pvp_stats (character_id, wins, losses, rating)
        VALUES (?, 0, 1, ?)
        ON CONFLICT(character_id) DO UPDATE SET
            losses = losses + 1,
            rating = rating - 15
    ''', (loser.id, 985))
    
    # Update match
    cursor.execute('''
        UPDATE pvp_matches
        SET status = ?, winner_id = ?, ended_at = ?, match_data = ?
        WHERE id = ?
    ''', ("completed", winner.id, datetime.datetime.now().isoformat(), 
          json.dumps({"combat_log": result["combat_log"]}), match_id))
    
    conn.commit()
    conn.close()
    
    # Notify both players
    await manager.send_personal_message(player1.user_id, {
        "type": "pvp_result",
        "match_id": match_id,
        "winner_id": winner.id,
        "winner_name": winner.name,
        "loser_name": loser.name,
        "gold_earned": gold_reward if winner.id == player1.id else 0,
        "exp_earned": exp_reward if winner.id == player1.id else 0,
        "combat_log": result["combat_log"][-10:]
    })
    
    await manager.send_personal_message(player2.user_id, {
        "type": "pvp_result",
        "match_id": match_id,
        "winner_id": winner.id,
        "winner_name": winner.name,
        "loser_name": loser.name,
        "gold_earned": gold_reward if winner.id == player2.id else 0,
        "exp_earned": exp_reward if winner.id == player2.id else 0,
        "combat_log": result["combat_log"][-10:]
    })
    
    return {
        "success": True,
        "winner_id": winner.id,
        "winner_name": winner.name,
        "loser_name": loser.name,
        "gold_reward": gold_reward,
        "exp_reward": exp_reward,
        "combat_log": result["combat_log"]
    }

# ==================== Guild Routes ====================

@app.post("/api/guilds")
async def create_guild(guild: GuildCreate, user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get character
    cursor.execute("SELECT id FROM characters WHERE user_id = ?", (user["id"],))
    character = cursor.fetchone()
    
    if not character:
        conn.close()
        raise HTTPException(status_code=404, detail="Character not found")
    
    # Check if already in a guild
    cursor.execute("SELECT guild_id FROM characters WHERE id = ?", (character["id"],))
    char_guild = cursor.fetchone()
    if char_guild and char_guild["guild_id"]:
        conn.close()
        raise HTTPException(status_code=400, detail="Already in a guild")
    
    # Check if guild name exists
    cursor.execute("SELECT * FROM guilds WHERE name = ?", (guild.name,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Guild name already exists")
    
    # Check if tag exists
    cursor.execute("SELECT * FROM guilds WHERE tag = ?", (guild.tag,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Guild tag already exists")
    
    # Create guild
    cursor.execute('''
        INSERT INTO guilds (name, tag, leader_id, created_at)
        VALUES (?, ?, ?, ?)
    ''', (guild.name, guild.tag, character["id"], datetime.datetime.now().isoformat()))
    
    guild_id = cursor.lastrowid
    
    # Add leader as member
    cursor.execute('''
        INSERT INTO guild_members (guild_id, character_id, rank, joined_at)
        VALUES (?, ?, ?, ?)
    ''', (guild_id, character["id"], "leader", datetime.datetime.now().isoformat()))
    
    # Update character guild
    cursor.execute('''
        UPDATE characters SET guild_id = ? WHERE id = ?
    ''', (guild_id, character["id"]))
    
    conn.commit()
    conn.close()
    
    return {"id": guild_id, "name": guild.name, "tag": guild.tag}

@app.get("/api/guilds/my-guild")
async def get_my_guild(user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get character
    cursor.execute("SELECT id, guild_id FROM characters WHERE user_id = ?", (user["id"],))
    character = cursor.fetchone()
    
    if not character or not character["guild_id"]:
        conn.close()
        return {"in_guild": False}
    
    # Get guild info
    cursor.execute("SELECT * FROM guilds WHERE id = ?", (character["guild_id"],))
    guild = cursor.fetchone()
    
    if not guild:
        conn.close()
        return {"in_guild": False}
    
    # Get members
    cursor.execute('''
        SELECT gm.*, c.name, c.level
        FROM guild_members gm
        JOIN characters c ON gm.character_id = c.id
        WHERE gm.guild_id = ?
    ''', (character["guild_id"],))
    members = cursor.fetchall()
    
    conn.close()
    
    return {
        "in_guild": True,
        "id": guild["id"],
        "name": guild["name"],
        "tag": guild["tag"],
        "level": guild["level"],
        "exp": guild["exp"],
        "leader_id": guild["leader_id"],
        "members": [{
            "character_id": m["character_id"],
            "name": m["name"],
            "level": m["level"],
            "rank": m["rank"],
            "joined_at": m["joined_at"]
        } for m in members]
    }

@app.post("/api/guilds/{guild_id}/invite/{character_id}")
async def invite_to_guild(guild_id: int, character_id: int, user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get character
    cursor.execute("SELECT id FROM characters WHERE user_id = ?", (user["id"],))
    character = cursor.fetchone()
    
    if not character:
        conn.close()
        raise HTTPException(status_code=404, detail="Character not found")
    
    # Check if in guild and has permission
    cursor.execute("SELECT * FROM guild_members WHERE character_id = ? AND guild_id = ?", 
                   (character["id"], guild_id))
    membership = cursor.fetchone()
    
    if not membership or membership["rank"] not in ["leader", "officer"]:
        conn.close()
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    # Check if target exists
    cursor.execute("SELECT user_id FROM characters WHERE id = ?", (character_id,))
    target = cursor.fetchone()
    
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="Target character not found")
    
    # Check if already in a guild
    cursor.execute("SELECT guild_id FROM characters WHERE id = ?", (character_id,))
    target_guild = cursor.fetchone()
    if target_guild and target_guild["guild_id"]:
        conn.close()
        raise HTTPException(status_code=400, detail="Target already in a guild")
    
    # Send invitation
    await manager.send_personal_message(target["user_id"], {
        "type": "guild_invite",
        "guild_id": guild_id,
        "inviter": character["id"]
    })
    
    conn.close()
    
    return {"success": True}

# ==================== Auction Routes ====================

@app.get("/api/auction/listings")
async def get_auctions(user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT a.*, c.name as seller_name, i.item_id
        FROM auctions a
        JOIN characters c ON a.seller_id = c.id
        JOIN inventory i ON a.item_instance_id = i.id
        WHERE a.status = 'active'
        ORDER BY a.created_at DESC
    ''')
    
    auctions = cursor.fetchall()
    conn.close()
    
    return [dict(a) for a in auctions]

@app.post("/api/auction/list")
async def list_auction(inventory_id: int, starting_bid: int, buyout_price: Optional[int] = None, 
                       duration_hours: int = 24, user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get character
    cursor.execute("SELECT id FROM characters WHERE user_id = ?", (user["id"],))
    character = cursor.fetchone()
    
    if not character:
        conn.close()
        raise HTTPException(status_code=404, detail="Character not found")
    
    # Get inventory item
    cursor.execute('''
        SELECT * FROM inventory
        WHERE id = ? AND character_id = ?
    ''', (inventory_id, character["id"]))
    item = cursor.fetchone()
    
    if not item:
        conn.close()
        raise HTTPException(status_code=404, detail="Item not found")
    
    # Calculate end time
    ends_at = (datetime.datetime.now() + datetime.timedelta(hours=duration_hours)).isoformat()
    
    # Create auction
    cursor.execute('''
        INSERT INTO auctions (seller_id, item_instance_id, starting_bid, buyout_price, ends_at)
        VALUES (?, ?, ?, ?, ?)
    ''', (character["id"], inventory_id, starting_bid, buyout_price, ends_at))
    
    # Remove item from inventory
    cursor.execute('''
        DELETE FROM inventory WHERE id = ?
    ''', (inventory_id,))
    
    conn.commit()
    conn.close()
    
    return {"success": True}

@app.post("/api/auction/{auction_id}/bid")
async def place_bid(auction_id: int, bid_amount: int, user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get character
    cursor.execute("SELECT id, gold FROM characters WHERE user_id = ?", (user["id"],))
    character = cursor.fetchone()
    
    if not character:
        conn.close()
        raise HTTPException(status_code=404, detail="Character not found")
    
    # Get auction
    cursor.execute("SELECT * FROM auctions WHERE id = ? AND status = 'active'", (auction_id,))
    auction = cursor.fetchone()
    
    if not auction:
        conn.close()
        raise HTTPException(status_code=404, detail="Auction not found")
    
    # Check if seller
    if auction["seller_id"] == character["id"]:
        conn.close()
        raise HTTPException(status_code=400, detail="Cannot bid on your own auction")
    
    # Check bid amount
    min_bid = auction["current_bid"] or auction["starting_bid"]
    if bid_amount <= min_bid:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Bid must be greater than {min_bid}")
    
    # Check gold
    if character["gold"] < bid_amount:
        conn.close()
        raise HTTPException(status_code=400, detail="Not enough gold")
    
    # If there was a previous bidder, refund them
    if auction["current_bidder_id"]:
        cursor.execute('''
            UPDATE characters
            SET gold = gold + ?
            WHERE id = ?
        ''', (auction["current_bid"], auction["current_bidder_id"]))
    
    # Place bid
    cursor.execute('''
        UPDATE auctions
        SET current_bid = ?, current_bidder_id = ?
        WHERE id = ?
    ''', (bid_amount, character["id"], auction_id))
    
    # Deduct gold from bidder
    cursor.execute('''
        UPDATE characters
        SET gold = gold - ?
        WHERE id = ?
    ''', (bid_amount, character["id"]))
    
    conn.commit()
    conn.close()
    
    return {"success": True}

# ==================== Leaderboard Routes ====================

@app.get("/api/leaderboard/level")
async def level_leaderboard(limit: int = 10):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT name, class_name, level, exp
        FROM characters
        ORDER BY level DESC, exp DESC
        LIMIT ?
    ''', (limit,))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(r) for r in rows]

@app.get("/api/leaderboard/gold")
async def gold_leaderboard(limit: int = 10):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT name, class_name, gold
        FROM characters
        ORDER BY gold DESC
        LIMIT ?
    ''', (limit,))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(r) for r in rows]

@app.get("/api/leaderboard/pvp")
async def pvp_leaderboard(limit: int = 10):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT c.name, c.class_name, ps.wins, ps.losses, ps.rating
        FROM pvp_stats ps
        JOIN characters c ON ps.character_id = c.id
        ORDER BY ps.rating DESC
        LIMIT ?
    ''', (limit,))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(r) for r in rows]

# ==================== Quest Routes ====================

@app.get("/api/quests")
async def get_quests(act: Optional[int] = None):
    if act:
        return [q for q in QUESTS.values() if q.get("act") == act]
    return list(QUESTS.values())

@app.post("/api/characters/{character_id}/quests/{quest_id}/start")
async def start_quest_route(character_id: int, quest_id: str, user=Depends(get_current_user)):
    result = await quest_engine.start_quest(character_id, quest_id)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result

@app.get("/api/characters/{character_id}/quests")
async def get_character_quests(character_id: int, user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor.execute('''
        SELECT * FROM quests
        WHERE character_id = ?
        ORDER BY 
            CASE status
                WHEN 'in_progress' THEN 1
                WHEN 'not_started' THEN 2
                WHEN 'completed' THEN 3
            END,
            started_at DESC
    ''', (character_id,))
    
    quests = cursor.fetchall()
    conn.close()
    
    return [dict(q) for q in quests]

# ==================== Story Routes ====================

@app.post("/api/characters/{character_id}/story/choice")
async def make_story_choice(character_id: int, quest_id: str, choice_id: str, user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Verify ownership
    cursor.execute("SELECT id FROM characters WHERE id = ? AND user_id = ?", (character_id, user["id"]))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=403, detail="Not your character")
    
    quest = QUESTS.get(quest_id)
    if not quest:
        conn.close()
        raise HTTPException(status_code=404, detail="Quest not found")
    
    choice = None
    for c in quest.get("choices", []):
        if c["id"] == choice_id:
            choice = c
            break
    
    if not choice:
        conn.close()
        raise HTTPException(status_code=404, detail="Choice not found")
    
    # Get or create story progression
    cursor.execute("SELECT * FROM story_progression WHERE character_id = ?", (character_id,))
    story = cursor.fetchone()
    
    if story:
        choices_made = json.loads(story["choices_made"] or "[]")
        choices_made.append({
            "quest_id": quest_id,
            "choice_id": choice_id,
            "choice_text": choice["text"],
            "timestamp": datetime.datetime.now().isoformat()
        })
        
        # Update reputation
        reputation = json.loads(story["reputation"] or "{}")
        for key, value in choice.get("reputation", {}).items():
            reputation[key] = reputation.get(key, 0) + value
        
        cursor.execute('''
            UPDATE story_progression
            SET choices_made = ?, reputation = ?
            WHERE character_id = ?
        ''', (json.dumps(choices_made), json.dumps(reputation), character_id))
    else:
        choices_made = [{
            "quest_id": quest_id,
            "choice_id": choice_id,
            "choice_text": choice["text"],
            "timestamp": datetime.datetime.now().isoformat()
        }]
        reputation = choice.get("reputation", {})
        
        cursor.execute('''
            INSERT INTO story_progression (character_id, choices_made, reputation)
            VALUES (?, ?, ?)
        ''', (character_id, json.dumps(choices_made), json.dumps(reputation)))
    
    # If this is the final quest, handle ending
    if quest.get("is_final"):
        ending = choice.get("ending", "The adventure continues...")
        cursor.execute('''
            UPDATE story_progression
            SET current_act = 6, completed_acts = ?
            WHERE character_id = ?
        ''', (json.dumps(list(range(1, 6))), character_id))
        
        # Grant bonus rewards based on ending
        if choice_id == "ending_hero":
            cursor.execute('''
                UPDATE characters
                SET gold = gold + 5000, exp = exp + 5000
                WHERE id = ?
            ''', (character_id,))
        elif choice_id == "ending_dark":
            cursor.execute('''
                UPDATE characters
                SET gold = gold + 10000, strength = strength + 50
                WHERE id = ?
            ''', (character_id,))
        elif choice_id == "ending_balance":
            cursor.execute('''
                UPDATE characters
                SET gold = gold + 3000, exp = exp + 3000,
                    intelligence = intelligence + 30, vitality = vitality + 30
                WHERE id = ?
            ''', (character_id,))
    
    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "choice_made": choice["text"],
        "ending": choice.get("ending") if quest.get("is_final") else None
    }

@app.get("/api/characters/{character_id}/story")
async def get_story_progress(character_id: int, user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Verify ownership
    cursor.execute("SELECT id FROM characters WHERE id = ? AND user_id = ?", (character_id, user["id"]))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=403, detail="Not your character")
    
    cursor.execute("SELECT * FROM story_progression WHERE character_id = ?", (character_id,))
    story = cursor.fetchone()
    
    # Get active quests
    cursor.execute('''
        SELECT q.*, qu.title, qu.act, qu.chapter
        FROM quests q
        JOIN json_each(?) as quest_data ON q.quest_id = quest_data.key
        LEFT JOIN json_each(?) as quests ON 1=1
    ''', (json.dumps(QUESTS), json.dumps([])))
    
    # Simplified - just get quests from DB
    cursor.execute('''
        SELECT q.*, ? as title, ? as act, ? as chapter
        FROM quests q
    ''', ("", 0, 0))
    
    active_quests = cursor.fetchall()
    
    conn.close()
    
    return {
        "story": dict(story) if story else None,
        "active_quests": [dict(q) for q in active_quests if q["status"] == "in_progress"],
        "completed_quests": [dict(q) for q in active_quests if q["status"] == "completed"]
    }

# ==================== Codex Routes ====================

@app.get("/api/codex")
async def get_codex():
    return CODEX

@app.post("/api/characters/{character_id}/codex/{entry_id}")
async def discover_codex(character_id: int, entry_id: str, user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Verify ownership
    cursor.execute("SELECT id FROM characters WHERE id = ? AND user_id = ?", (character_id, user["id"]))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=403, detail="Not your character")
    
    if entry_id not in CODEX:
        conn.close()
        raise HTTPException(status_code=404, detail="Codex entry not found")
    
    cursor.execute('''
        INSERT OR IGNORE INTO codex (character_id, entry_id, discovered_at)
        VALUES (?, ?, ?)
    ''', (character_id, entry_id, datetime.datetime.now().isoformat()))
    
    conn.commit()
    conn.close()
    
    return {"success": True}

@app.get("/api/characters/{character_id}/codex")
async def get_character_codex(character_id: int, user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Verify ownership
    cursor.execute("SELECT id FROM characters WHERE id = ? AND user_id = ?", (character_id, user["id"]))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=403, detail="Not your character")
    
    cursor.execute("SELECT * FROM codex WHERE character_id = ?", (character_id,))
    entries = cursor.fetchall()
    conn.close()
    
    discovered = {e["entry_id"]: dict(e) for e in entries}
    
    return {
        "discovered": discovered,
        "all_entries": CODEX
    }

# ==================== Companion Routes ====================

@app.get("/api/companions")
async def get_companions():
    return COMPANIONS

@app.post("/api/characters/{character_id}/companions/{companion_id}/recruit")
async def recruit_companion(character_id: int, companion_id: str, user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Verify ownership
    cursor.execute("SELECT id FROM characters WHERE id = ? AND user_id = ?", (character_id, user["id"]))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=403, detail="Not your character")
    
    if companion_id not in COMPANIONS:
        conn.close()
        raise HTTPException(status_code=404, detail="Companion not found")
    
    # Get story progression
    cursor.execute("SELECT companions FROM story_progression WHERE character_id = ?", (character_id,))
    story = cursor.fetchone()
    
    if story:
        companions = json.loads(story["companions"] or "[]")
        if companion_id in companions:
            conn.close()
            raise HTTPException(status_code=400, detail="Companion already recruited")
        
        companions.append(companion_id)
        cursor.execute('''
            UPDATE story_progression
            SET companions = ?
            WHERE character_id = ?
        ''', (json.dumps(companions), character_id))
    else:
        cursor.execute('''
            INSERT INTO story_progression (character_id, companions)
            VALUES (?, ?)
        ''', (character_id, json.dumps([companion_id])))
    
    conn.commit()
    conn.close()
    
    return {"success": True, "companion": COMPANIONS[companion_id]}

# ==================== Inventory Routes ====================

@app.post("/api/characters/{character_id}/inventory/use/{inventory_id}")
async def use_item(character_id: int, inventory_id: int, user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Verify ownership
    cursor.execute('''
        SELECT i.*, c.hp, c.max_hp, c.mana, c.max_mana
        FROM inventory i
        JOIN characters c ON i.character_id = c.id
        WHERE i.id = ? AND c.user_id = ?
    ''', (inventory_id, user["id"]))
    
    item = cursor.fetchone()
    
    if not item:
        conn.close()
        raise HTTPException(status_code=404, detail="Item not found")
    
    item_data = ITEMS.get(item["item_id"])
    if not item_data:
        conn.close()
        raise HTTPException(status_code=404, detail="Item data not found")
    
    effect = item_data.get("effect", {})
    
    # Apply effects
    if "heal" in effect:
        new_hp = min(item["max_hp"], item["hp"] + effect["heal"])
        cursor.execute('''
            UPDATE characters SET hp = ? WHERE id = ?
        ''', (new_hp, character_id))
    
    if "restore_mana" in effect:
        new_mana = min(item["max_mana"], item["mana"] + effect["restore_mana"])
        cursor.execute('''
            UPDATE characters SET mana = ? WHERE id = ?
        ''', (new_mana, character_id))
    
    # Remove used item
    if item["quantity"] > 1:
        cursor.execute('''
            UPDATE inventory SET quantity = quantity - 1 WHERE id = ?
        ''', (inventory_id,))
    else:
        cursor.execute('''
            DELETE FROM inventory WHERE id = ?
        ''', (inventory_id,))
    
    conn.commit()
    conn.close()
    
    return {"success": True, "effect": effect}

# ==================== Owner/Admin Routes ====================

@app.post("/api/admin/cheat")
async def cheat_command(
    command: CheatCommand,
    user = Depends(require_owner)
):
    """Owner-only cheat commands"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        target_char = None
        
        if command.target:
            # Find target by name
            cursor.execute("SELECT * FROM characters WHERE name = ?", (command.target,))
            target_char = cursor.fetchone()
            
            if not target_char:
                cursor.execute("SELECT * FROM users WHERE username = ?", (command.target,))
                target_user = cursor.fetchone()
                if target_user:
                    cursor.execute("SELECT * FROM characters WHERE user_id = ?", (target_user["id"],))
                    target_char = cursor.fetchone()
        else:
            # Use current user's character
            cursor.execute("SELECT * FROM characters WHERE user_id = ?", (user["id"],))
            target_char = cursor.fetchone()
        
        if not target_char:
            conn.close()
            return {"success": False, "message": "Target character not found"}
        
        if command.command == "give_gold":
            amount = command.amount or 1000
            cursor.execute('''
                UPDATE characters SET gold = gold + ? WHERE id = ?
            ''', (amount, target_char["id"]))
            
            await manager.send_personal_message(target_char["user_id"], {
                "type": "cheat",
                "message": f"You received {amount} gold from owner"
            })
            
            message = f"Gave {amount} gold to {target_char['name']}"
        
        elif command.command == "set_level":
            amount = command.amount or 100
            cursor.execute('''
                UPDATE characters SET level = ?, exp = 0 WHERE id = ?
            ''', (amount, target_char["id"]))
            
            await manager.send_personal_message(target_char["user_id"], {
                "type": "cheat",
                "message": f"Your level has been set to {amount}"
            })
            
            message = f"Set {target_char['name']} to level {amount}"
        
        elif command.command == "spawn_item":
            if command.item_id and command.item_id in ITEMS:
                cursor.execute('''
                    INSERT INTO inventory (character_id, item_id, quantity)
                    VALUES (?, ?, ?)
                ''', (target_char["id"], command.item_id, command.amount or 1))
                
                await manager.send_personal_message(target_char["user_id"], {
                    "type": "cheat",
                    "message": f"You received item: {ITEMS[command.item_id]['name']}"
                })
                
                message = f"Spawned {ITEMS[command.item_id]['name']} for {target_char['name']}"
            else:
                conn.close()
                return {"success": False, "message": "Invalid item ID"}
        
        elif command.command == "god_mode":
            cursor.execute('''
                UPDATE characters
                SET hp = 99999, max_hp = 99999, mana = 99999, max_mana = 99999,
                    strength = 9999, agility = 9999, intelligence = 9999, vitality = 9999
                WHERE id = ?
            ''', (target_char["id"],))
            
            await manager.send_personal_message(target_char["user_id"], {
                "type": "cheat",
                "message": "God mode activated!"
            })
            
            message = f"God mode activated for {target_char['name']}"
        
        elif command.command == "kill":
            cursor.execute('''
                UPDATE characters SET hp = 0 WHERE id = ?
            ''', (target_char["id"],))
            
            await manager.send_personal_message(target_char["user_id"], {
                "type": "cheat",
                "message": "You have been killed by owner"
            })
            
            message = f"Killed {target_char['name']}"
        
        elif command.command == "heal":
            cursor.execute('''
                UPDATE characters SET hp = max_hp, mana = max_mana WHERE id = ?
            ''', (target_char["id"],))
            
            await manager.send_personal_message(target_char["user_id"], {
                "type": "cheat",
                "message": "You have been fully healed"
            })
            
            message = f"Healed {target_char['name']}"
        
        elif command.command == "complete_quests":
            cursor.execute('''
                UPDATE quests SET status = 'completed', completed_at = ?
                WHERE character_id = ? AND status = 'in_progress'
            ''', (datetime.datetime.now().isoformat(), target_char["id"]))
            
            message = f"Completed all quests for {target_char['name']}"
        
        else:
            conn.close()
            return {"success": False, "message": "Invalid command"}
        
        conn.commit()
        conn.close()
        
        return {"success": True, "message": message}
        
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/mod/action")
async def mod_action(
    action: ModAction,
    user = Depends(require_mod)
):
    """Moderator actions"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        target = action.target_user_id
        
        if action.action == "kick":
            # Disconnect user
            if target in manager.active_connections:
                await manager.send_personal_message(target, {
                    "type": "system",
                    "message": f"You have been kicked. Reason: {action.reason}"
                })
                await manager.active_connections[target].close()
                manager.disconnect(target)
            
            # Log action
            cursor.execute('''
                INSERT INTO mod_logs (moderator_id, target_id, action, reason, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (user["id"], target, "kick", action.reason, datetime.datetime.now().isoformat()))
            
            message = f"Kicked user {target}"
        
        elif action.action == "ban":
            # Ban user
            manager.banned_users.add(target)
            if target in manager.active_connections:
                await manager.active_connections[target].close()
                manager.disconnect(target)
            
            cursor.execute('''
                UPDATE users SET banned = 1, ban_reason = ? WHERE id = ?
            ''', (action.reason, target))
            
            cursor.execute('''
                INSERT INTO mod_logs (moderator_id, target_id, action, reason, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (user["id"], target, "ban", action.reason, datetime.datetime.now().isoformat()))
            
            message = f"Banned user {target}"
        
        elif action.action == "unban":
            manager.banned_users.discard(target)
            
            cursor.execute('''
                UPDATE users SET banned = 0, ban_reason = NULL WHERE id = ?
            ''', (target,))
            
            message = f"Unbanned user {target}"
        
        elif action.action == "mute":
            duration = action.duration or 60
            mute_until = datetime.datetime.now() + datetime.timedelta(minutes=duration)
            manager.muted_users[target] = mute_until
            
            await manager.send_personal_message(target, {
                "type": "system",
                "message": f"You have been muted for {duration} minutes. Reason: {action.reason}"
            })
            
            cursor.execute('''
                INSERT INTO mod_logs (moderator_id, target_id, action, reason, duration, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user["id"], target, "mute", action.reason, duration, datetime.datetime.now().isoformat()))
            
            message = f"Muted user {target} for {duration} minutes"
        
        elif action.action == "unmute":
            if target in manager.muted_users:
                del manager.muted_users[target]
                
                await manager.send_personal_message(target, {
                    "type": "system",
                    "message": "You have been unmuted"
                })
            
            message = f"Unmuted user {target}"
        
        elif action.action == "warn":
            cursor.execute('''
                INSERT INTO user_warnings (user_id, moderator_id, reason, created_at)
                VALUES (?, ?, ?, ?)
            ''', (target, user["id"], action.reason, datetime.datetime.now().isoformat()))
            
            await manager.send_personal_message(target, {
                "type": "system",
                "message": f"You have received a warning. Reason: {action.reason}"
            })
            
            # Get warning count
            cursor.execute('''
                SELECT COUNT(*) as count FROM user_warnings WHERE user_id = ?
            ''', (target,))
            count = cursor.fetchone()["count"]
            
            message = f"Warned user {target} (Warning #{count})"
        
        else:
            conn.close()
            return {"success": False, "message": "Invalid action"}
        
        conn.commit()
        conn.close()
        
        return {"success": True, "message": message}
        
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/admin/event")
async def create_game_event(
    event: GameEvent,
    user = Depends(require_admin)
):
    """Create global game events"""
    
    if event.event_type == "spawn_boss":
        boss_data = event.data
        await manager.broadcast_to_room("global", {
            "type": "boss_spawn",
            "boss": boss_data.get("name", "Unknown Boss"),
            "location": boss_data.get("location", "Unknown"),
            "level": boss_data.get("level", 50),
            "hp": boss_data.get("hp", 1000),
            "rewards": boss_data.get("rewards", {})
        })
        
        return {"success": True, "message": f"Boss {boss_data.get('name')} spawned"}
    
    elif event.event_type == "start_event":
        event_data = event.data
        await manager.broadcast_to_room("global", {
            "type": "global_event",
            "event": event_data.get("name"),
            "description": event_data.get("description"),
            "duration": event_data.get("duration", 60),
            "rewards": event_data.get("rewards", {})
        })
        
        return {"success": True, "message": f"Event {event_data.get('name')} started"}
    
    elif event.event_type == "give_rewards":
        reward_data = event.data
        gold_amount = reward_data.get("gold", 0)
        item_id = reward_data.get("item_id")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        for user_id in manager.rooms["global"]:
            cursor.execute("SELECT id FROM characters WHERE user_id = ?", (user_id,))
            char = cursor.fetchone()
            
            if char:
                if gold_amount > 0:
                    cursor.execute('''
                        UPDATE characters SET gold = gold + ? WHERE id = ?
                    ''', (gold_amount, char["id"]))
                
                if item_id:
                    cursor.execute('''
                        INSERT INTO inventory (character_id, item_id, quantity)
                        VALUES (?, ?, 1)
                    ''', (char["id"], item_id))
                
                await manager.send_personal_message(user_id, {
                    "type": "reward",
                    "gold": gold_amount,
                    "item": item_id,
                    "message": f"You received {gold_amount} gold" + (f" and {item_id}" if item_id else "")
                })
        
        conn.commit()
        conn.close()
        
        return {"success": True, "message": "Rewards distributed"}
    
    elif event.event_type == "announce":
        announcement = event.data.get("message", "Announcement")
        await manager.broadcast_to_room("global", {
            "type": "announcement",
            "message": announcement,
            "from": user.get('username', 'Admin')
        })
        
        return {"success": True, "message": "Announcement sent"}
    
    return {"success": False, "message": "Invalid event type"}

@app.get("/api/admin/online-players")
async def get_online_players(user = Depends(require_mod)):
    """Get list of online players"""
    players = []
    for user_id in manager.rooms["global"]:
        role = manager.user_roles.get(user_id, "player")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name, level FROM characters WHERE user_id = ?", (user_id,))
        char = cursor.fetchone()
        conn.close()
        
        char_name = char["name"] if char else "Unknown"
        char_level = char["level"] if char else 0
        
        players.append({
            "user_id": user_id,
            "character_name": char_name,
            "level": char_level,
            "role": role,
            "is_muted": user_id in manager.muted_users,
            "is_banned": user_id in manager.banned_users
        })
    
    return {
        "online_count": len(players),
        "players": players,
        "timestamp": datetime.datetime.now().isoformat()
    }

@app.post("/api/admin/promote")
async def promote_user(
    target_user_id: int,
    new_role: str,
    user = Depends(require_owner)
):
    """Promote user to moderator/admin"""
    if new_role not in ["moderator", "admin"]:
        raise HTTPException(status_code=400, detail="Invalid role")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE users SET role = ? WHERE id = ?
    ''', (new_role, target_user_id))
    
    cursor.execute('''
        UPDATE characters SET role = ? WHERE user_id = ?
    ''', (new_role, target_user_id))
    
    conn.commit()
    conn.close()
    
    # Update connection manager
    if target_user_id in manager.user_roles:
        manager.user_roles[target_user_id] = new_role
        if new_role in ["moderator", "admin"]:
            manager.rooms["mod"].add(target_user_id)
        if new_role == "admin":
            manager.rooms["admin"].add(target_user_id)
    
    await manager.send_personal_message(target_user_id, {
        "type": "system",
        "message": f"You have been promoted to {new_role}!"
    })
    
    return {"success": True, "message": f"User promoted to {new_role}"}

# ==================== WebSocket Routes ====================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=1008)
        return
    
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = int(payload.get("sub"))
        role = payload.get("role", "player")
        is_owner = payload.get("is_owner", False)
        
        # Check if banned
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT banned FROM users WHERE id = ?", (user_id,))
        user = cursor.fetchone()
        conn.close()
        
        if user and user["banned"]:
            await websocket.close(code=1008)
            return
        
        if user_id in manager.banned_users:
            await websocket.close(code=1008)
            return
        
    except Exception:
        await websocket.close(code=1008)
        return
    
    await manager.connect(websocket, user_id, role)
    
    # Send welcome message
    role_emoji = "👑" if is_owner else "⚡" if role == "admin" else "🛡️" if role == "moderator" else ""
    await manager.send_personal_message(user_id, {
        "type": "system",
        "message": f"Connected to server {role_emoji}".strip()
    })
    
    # Send recent chat history
    for msg in manager.chat_history[-20:]:
        await manager.send_personal_message(user_id, msg)
    
    try:
        while True:
            data = await websocket.receive_json()
            
            msg_type = data.get("type")
            
            if msg_type == "chat":
                message = ChatMessage(
                    channel=data["channel"],
                    content=data["content"],
                    target=data.get("target")
                )
                await manager.handle_chat_message(user_id, message)
            
            elif msg_type == "move":
                character_id = data.get("character_id")
                x = data.get("x")
                y = data.get("y")
                
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE characters SET x_position = ?, y_position = ?
                    WHERE id = ? AND user_id = ?
                ''', (x, y, character_id, user_id))
                conn.commit()
                conn.close()
                
                await manager.broadcast_to_room("global", {
                    "type": "movement",
                    "user_id": user_id,
                    "character_id": character_id,
                    "x": x,
                    "y": y
                }, exclude_user=user_id)
            
            elif msg_type == "emote":
                emote = data.get("emote")
                await manager.broadcast_to_room("global", {
                    "type": "emote",
                    "user_id": user_id,
                    "emote": emote
                }, exclude_user=user_id)
            
            elif msg_type == "trade_update" and activeTrade:
                # Handle trade update via WebSocket
                trade_id = data.get("trade_id")
                gold = data.get("gold", 0)
                items = data.get("items", [])
                
                # Update in database
                conn = get_db_connection()
                cursor = conn.cursor()
                
                cursor.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
                trade = cursor.fetchone()
                
                if trade:
                    if trade["initiator_id"] == manager.character_ids.get(user_id):
                        cursor.execute('''
                            UPDATE trades SET initiator_items = ?, initiator_gold = ?
                            WHERE id = ?
                        ''', (json.dumps(items), gold, trade_id))
                    elif trade["target_id"] == manager.character_ids.get(user_id):
                        cursor.execute('''
                            UPDATE trades SET target_items = ?, target_gold = ?
                            WHERE id = ?
                        ''', (json.dumps(items), gold, trade_id))
                
                conn.commit()
                conn.close()
    
    except WebSocketDisconnect:
        manager.disconnect(user_id)

# ==================== Health Check ====================

@app.get("/", include_in_schema=True)
async def root():
    return {
        "status": "online",
        "service": "Pixel RPG API",
        "version": "3.0.0",
        "features": ["story_mode", "multiplayer", "trading", "pvp", "guilds", "auction_house"],
        "timestamp": datetime.datetime.now().isoformat()
    }

@app.get("/health")
async def health_check():
    # Check database connection
    db_status = "healthy"
    try:
        conn = get_db_connection()
        conn.execute("SELECT 1").fetchone()
        conn.close()
    except:
        db_status = "unhealthy"
    
    return {
        "status": "healthy",
        "database": db_status,
        "websocket_connections": len(manager.active_connections),
        "timestamp": datetime.datetime.now().isoformat()
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)



