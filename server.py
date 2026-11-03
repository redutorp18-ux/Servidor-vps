#!/usr/bin/env python3
"""Independent local server for the FF170 project.

This is a documented, original JSON-lines protocol. It is not the Garena protocol
and is not claimed to be compatible with the original client.
"""
from __future__ import annotations
import asyncio, hashlib, hmac, json, os, secrets, sqlite3, time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
_config_candidates = (ROOT / "server/config.json", Path(__file__).resolve().with_name("config.json"), ROOT / "config.json")
CONFIG_PATH = next((path for path in _config_candidates if path.exists()), _config_candidates[0])
CONFIG = json.loads(CONFIG_PATH.read_text())
DB_PATH = ROOT / CONFIG["database"]
TOKEN_TTL = int(CONFIG.get("session_ttl_seconds", 86400))


def now() -> int:
    return int(time.time())


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 180_000)
    return f"pbkdf2_sha256$180000${salt.hex()}${digest.hex()}"


def password_check(password: str, encoded: str) -> bool:
    try:
        scheme, rounds, salt_hex, digest_hex = encoded.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(got.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


@dataclass
class Room:
    room_id: str
    owner_id: str
    mode: str
    map_name: str
    capacity: int
    state: str = "waiting"
    members: set[str] = field(default_factory=set)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(
          id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL, level INTEGER NOT NULL DEFAULT 1,
          xp INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL,
          wins INTEGER NOT NULL DEFAULT 0, kills INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sessions(
          token TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at INTEGER NOT NULL,
          expires_at INTEGER NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS rooms(
          id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, mode TEXT NOT NULL,
          map_name TEXT NOT NULL, capacity INTEGER NOT NULL, state TEXT NOT NULL,
          created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS room_members(
          room_id TEXT NOT NULL, user_id TEXT NOT NULL,
          PRIMARY KEY(room_id,user_id));
        CREATE TABLE IF NOT EXISTS matches(
          id TEXT PRIMARY KEY, room_id TEXT NOT NULL, mode TEXT NOT NULL,
          map_name TEXT NOT NULL, state TEXT NOT NULL, created_at INTEGER NOT NULL,
          ended_at INTEGER);
        CREATE TABLE IF NOT EXISTS matchmaking_queue(
          user_id TEXT PRIMARY KEY, mode TEXT NOT NULL, map_name TEXT NOT NULL,
          joined_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS match_players(
          match_id TEXT NOT NULL, user_id TEXT NOT NULL,
          joined_at INTEGER NOT NULL, PRIMARY KEY(match_id,user_id));
        CREATE TABLE IF NOT EXISTS player_states(
          match_id TEXT NOT NULL, user_id TEXT NOT NULL,
          x REAL NOT NULL DEFAULT 0, y REAL NOT NULL DEFAULT 0,
          hp INTEGER NOT NULL DEFAULT 100, updated_at INTEGER NOT NULL,
          PRIMARY KEY(match_id,user_id));
        CREATE TABLE IF NOT EXISTS player_profiles(
          user_id TEXT PRIMARY KEY, character_id TEXT NOT NULL DEFAULT 'default',
          mode TEXT NOT NULL DEFAULT 'training', updated_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS inventory_items(
          user_id TEXT NOT NULL, item_id TEXT NOT NULL, kind TEXT NOT NULL,
          quantity INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_id,item_id));
        """)
        self.db.commit()

    def user(self, user_id: str):
        return self.db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()

    def create_user(self, username: str, password: str):
        uid = secrets.token_hex(12)
        self.db.execute("INSERT INTO users(id,username,password_hash,created_at) VALUES(?,?,?,?)",
                        (uid, username, password_hash(password), now()))
        self.db.commit()
        return self.user(uid)

    def find_user(self, username: str):
        return self.db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()

    def session(self, token: str):
        row = self.db.execute("SELECT u.*,s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?", (token,)).fetchone()
        return row if row and row["expires_at"] >= now() else None

    def make_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        self.db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (token,user_id,now(),now()+TOKEN_TTL))
        self.db.commit(); return token

    def save_room(self, r: Room):
        self.db.execute("INSERT INTO rooms VALUES(?,?,?,?,?,?,?)", (r.room_id,r.owner_id,r.mode,r.map_name,r.capacity,r.state,now()))
        for uid in r.members: self.db.execute("INSERT INTO room_members VALUES(?,?)", (r.room_id,uid))
        self.db.commit()

    def add_member(self, room_id: str, user_id: str):
        self.db.execute("INSERT OR IGNORE INTO room_members VALUES(?,?)", (room_id,user_id)); self.db.commit()

    def queue_user(self, user_id: str, mode: str, map_name: str):
        self.db.execute("INSERT OR REPLACE INTO matchmaking_queue VALUES(?,?,?,?)", (user_id, mode, map_name, now()))
        self.db.commit()

    def dequeue_user(self, user_id: str):
        self.db.execute("DELETE FROM matchmaking_queue WHERE user_id=?", (user_id,)); self.db.commit()

    def ensure_profile(self, user_id: str):
        self.db.execute("INSERT OR IGNORE INTO player_profiles(user_id,updated_at) VALUES(?,?)", (user_id, now()))
        self.db.execute("INSERT OR IGNORE INTO inventory_items VALUES(?,?,?,?)", (user_id, "default_character", "character", 1))
        self.db.commit()

    def inventory(self, user_id: str):
        self.ensure_profile(user_id)
        rows = self.db.execute("SELECT item_id,kind,quantity FROM inventory_items WHERE user_id=? ORDER BY item_id", (user_id,)).fetchall()
        return [dict(row) for row in rows]

    def online_users(self):
        rows = self.db.execute("SELECT id,username,level,xp FROM users ORDER BY username").fetchall()
        return [dict(row) for row in rows]


class GameServer:
    def __init__(self):
        self.store = Store(DB_PATH)
        self.rooms: dict[str,Room] = {}
        self.clients: dict[str, asyncio.StreamWriter] = {}
        self.lock = asyncio.Lock()

    def response(self, request_id, ok=True, **data):
        return {"request_id": request_id, "ok": ok, **data}

    async def handle(self, req: dict[str,Any]):
        rid=req.get("request_id"); action=req.get("action")
        if action == "register":
            username=str(req.get("username","")).strip(); password=str(req.get("password", ""))
            if len(username)<3 or len(username)>32 or len(password)<8: return self.response(rid,False,error="invalid_credentials")
            try: u=self.store.create_user(username,password)
            except sqlite3.IntegrityError: return self.response(rid,False,error="username_exists")
            return self.response(rid,user=self.public_user(u))
        if action == "login":
            u=self.store.find_user(str(req.get("username","")))
            if not u or not password_check(str(req.get("password","")),u["password_hash"]): return self.response(rid,False,error="invalid_login")
            token=self.store.make_session(u["id"]); return self.response(rid,token=token,user=self.public_user(u))
        token=req.get("token"); u=self.store.session(token) if isinstance(token,str) else None
        if not u: return self.response(rid,False,error="unauthorized")
        uid=u["id"]
        if action == "heartbeat": return self.response(rid,server_time=now())
        if action == "profile":
            self.store.ensure_profile(uid)
            profile = self.store.db.execute("SELECT character_id,mode,updated_at FROM player_profiles WHERE user_id=?", (uid,)).fetchone()
            return self.response(rid,user=self.public_user(u),profile=dict(profile),inventory=self.store.inventory(uid))
        if action == "inventory":
            return self.response(rid, inventory=self.store.inventory(uid))
        if action == "list_online":
            return self.response(rid, players=self.store.online_users())
        if action == "select_character":
            character_id=str(req.get("character_id", "default"))[:64]
            self.store.ensure_profile(uid)
            self.store.db.execute("UPDATE player_profiles SET character_id=?,updated_at=? WHERE user_id=?", (character_id,now(),uid)); self.store.db.commit()
            return self.response(rid,character_id=character_id)
        if action == "list_rooms": return self.response(rid,rooms=[self.room_json(x) for x in self.rooms.values()])
        if action == "queue_leave":
            self.store.dequeue_user(uid)
            return self.response(rid,queued=False)
        if action == "queue_join":
            mode=str(req.get("mode","training")); mp=str(req.get("map","training_ground"))
            self.store.queue_user(uid, mode, mp)
            rows=self.store.db.execute("SELECT user_id FROM matchmaking_queue WHERE mode=? AND map_name=? ORDER BY joined_at LIMIT 2", (mode,mp)).fetchall()
            if len(rows)<2:
                return self.response(rid,queued=True,match=None,mode=mode,map=mp)
            players=[row["user_id"] for row in rows]; mid=secrets.token_hex(10)
            self.store.db.execute("INSERT INTO matches VALUES(?,?,?,?,?,?,?)", (mid,"queue",mode,mp,"running",now(),None))
            for player_id in players:
                self.store.db.execute("INSERT INTO match_players VALUES(?,?,?)", (mid,player_id,now()))
                self.store.db.execute("INSERT INTO player_states VALUES(?,?,?,?,?,?)", (mid,player_id,0.0,0.0,100,now()))
                self.store.db.execute("DELETE FROM matchmaking_queue WHERE user_id=?", (player_id,))
            self.store.db.commit()
            return self.response(rid,queued=False,match=self.match_json(mid))
        if action == "match_state":
            mid=str(req.get("match_id")); match=self.store.db.execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone()
            if not match: return self.response(rid,False,error="match_not_found")
            member=self.store.db.execute("SELECT 1 FROM match_players WHERE match_id=? AND user_id=?", (mid,uid)).fetchone()
            if not member: return self.response(rid,False,error="not_match_player")
            return self.response(rid,match=self.match_json(mid))
        if action == "update_player_state":
            mid=str(req.get("match_id")); member=self.store.db.execute("SELECT 1 FROM match_players WHERE match_id=? AND user_id=?", (mid,uid)).fetchone()
            if not member: return self.response(rid,False,error="not_match_player")
            try: x=float(req.get("x",0)); y=float(req.get("y",0)); hp=max(0,min(100,int(req.get("hp",100))))
            except (TypeError,ValueError): return self.response(rid,False,error="invalid_player_state")
            self.store.db.execute("UPDATE player_states SET x=?,y=?,hp=?,updated_at=? WHERE match_id=? AND user_id=?", (x,y,hp,now(),mid,uid)); self.store.db.commit()
            return self.response(rid,state={"match_id":mid,"user_id":uid,"x":x,"y":y,"hp":hp,"updated_at":now()})
        if action == "apply_damage":
            mid=str(req.get("match_id")); target=str(req.get("target_user_id", ""))
            attacker=self.store.db.execute("SELECT 1 FROM match_players WHERE match_id=? AND user_id=?", (mid,uid)).fetchone()
            victim=self.store.db.execute("SELECT hp FROM player_states WHERE match_id=? AND user_id=?", (mid,target)).fetchone()
            if not attacker or not victim: return self.response(rid,False,error="not_match_player")
            try: damage=max(0,min(100,int(req.get("damage",0))))
            except (TypeError,ValueError): return self.response(rid,False,error="invalid_damage")
            hp=max(0,int(victim["hp"])-damage)
            self.store.db.execute("UPDATE player_states SET hp=?,updated_at=? WHERE match_id=? AND user_id=?", (hp,now(),mid,target)); self.store.db.commit()
            return self.response(rid,damage=damage,target_user_id=target,hp=hp,eliminated=(hp==0))
        if action == "leave_match":
            mid=str(req.get("match_id")); self.store.db.execute("DELETE FROM match_players WHERE match_id=? AND user_id=?", (mid,uid)); self.store.db.execute("DELETE FROM player_states WHERE match_id=? AND user_id=?", (mid,uid)); self.store.db.commit()
            left=self.store.db.execute("SELECT COUNT(*) AS n FROM match_players WHERE match_id=?", (mid,)).fetchone()["n"]
            if left==0: self.store.db.execute("UPDATE matches SET state='ended',ended_at=? WHERE id=?", (now(),mid)); self.store.db.commit()
            return self.response(rid,left_match=True)
        if action == "create_room":
            cap=max(2,min(int(req.get("capacity",4)),64)); mode=str(req.get("mode","training")); mp=str(req.get("map","training_ground"))
            room=Room(secrets.token_hex(8),uid,mode,mp,cap,members={uid}); self.rooms[room.room_id]=room; self.store.save_room(room)
            return self.response(rid,room=self.room_json(room))
        room=self.rooms.get(str(req.get("room_id")))
        if action == "join_room":
            if not room: return self.response(rid,False,error="room_not_found")
            if room.state != "waiting": return self.response(rid,False,error="room_not_waiting")
            if len(room.members)>=room.capacity: return self.response(rid,False,error="room_full")
            room.members.add(uid); self.store.add_member(room.room_id,uid); return self.response(rid,room=self.room_json(room))
        if action == "leave_room":
            if not room: return self.response(rid,False,error="room_not_found")
            room.members.discard(uid)
            if uid==room.owner_id and room.members: room.owner_id=next(iter(room.members))
            if not room.members: self.rooms.pop(room.room_id,None)
            return self.response(rid,room=self.room_json(room) if room.members else None)
        if action == "start_match":
            if not room: return self.response(rid,False,error="room_not_found")
            if room.owner_id != uid: return self.response(rid,False,error="not_owner")
            room.state="in_match"; mid=secrets.token_hex(10)
            self.store.db.execute("INSERT INTO matches VALUES(?,?,?,?,?,?,?)",(mid,room.room_id,room.mode,room.map_name,"running",now(),None)); self.store.db.commit()
            for player_id in room.members:
                self.store.db.execute("INSERT OR IGNORE INTO match_players VALUES(?,?,?)", (mid,player_id,now()))
                self.store.db.execute("INSERT OR IGNORE INTO player_states VALUES(?,?,?,?,?,?)", (mid,player_id,0.0,0.0,100,now()))
            self.store.db.commit()
            return self.response(rid,match={"id":mid,"mode":room.mode,"map":room.map_name,"players":len(room.members),"state":"running"})
        return self.response(rid,False,error="unknown_action")

    @staticmethod
    def public_user(u):
        return {"id":u["id"],"username":u["username"],"level":u["level"],"xp":u["xp"],"wins":u["wins"],"kills":u["kills"]}

    @staticmethod
    def room_json(r: Room):
        return {"id":r.room_id,"owner_id":r.owner_id,"mode":r.mode,"map":r.map_name,"capacity":r.capacity,"state":r.state,"members":sorted(r.members)}

    def match_json(self, match_id: str):
        match=self.store.db.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
        players=self.store.db.execute("SELECT user_id,x,y,hp,updated_at FROM player_states WHERE match_id=? ORDER BY user_id", (match_id,)).fetchall()
        return {"id":match["id"],"mode":match["mode"],"map":match["map_name"],"state":match["state"],"created_at":match["created_at"],"players":[dict(p) for p in players]}

    async def client(self, reader, writer):
        peer=writer.get_extra_info("peername")
        try:
            async for raw in reader:
                if len(raw)>1_000_000: break
                try: req=json.loads(raw)
                except json.JSONDecodeError: writer.write((json.dumps({"ok":False,"error":"invalid_json"})+'\n').encode()); await writer.drain(); continue
                try: out=await self.handle(req)
                except Exception as e:
                    print(f"request error from {peer}: {e}"); out=self.response(req.get("request_id"),False,error="internal_error")
                writer.write((json.dumps(out,separators=(",",":"))+"\n").encode()); await writer.drain()
        finally: writer.close(); await writer.wait_closed()

async def main():
    s=GameServer(); server=await asyncio.start_server(s.client,CONFIG["host"],int(CONFIG["port"]))
    print(f"ff170-independent-server listening on {CONFIG['host']}:{CONFIG['port']}")
    async with server: await server.serve_forever()

if __name__ == "__main__": asyncio.run(main())
