from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date
from sqlalchemy.orm import sessionmaker, Session, DeclarativeBase
from datetime import datetime, date
from typing import Optional
import uuid
import random
import string
import hashlib
import os

# --- 資料庫設定 ---
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./memories.db")
if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(SQLALCHEMY_DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    pass

# --- 資料庫模型 ---
class DBUser(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)

class DBRoom(Base):
    __tablename__ = "rooms"
    id = Column(String, primary_key=True, index=True) 
    name = Column(String) 
    link_code = Column(String, unique=True, index=True) 
    start_date = Column(Date, nullable=True)
    meetup_count = Column(Integer, default=0)

class DBUserRoom(Base):
    __tablename__ = "user_rooms"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, index=True)
    room_id = Column(String, index=True)

class DBMemory(Base):
    __tablename__ = "memories"
    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(String, index=True) 
    date = Column(Date)
    content = Column(String)
    author = Column(String)

# --- 密碼與驗證 ---
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

def get_password_hash(password): return hashlib.sha256(password.encode()).hexdigest()
def verify_password(plain_password, hashed_password): return get_password_hash(plain_password) == hashed_password
def generate_code(): return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

app = FastAPI(title="Multi-Room Shared Memory API")

# 【終極修復】在伺服器啟動時，強制拆除並重建表格
@app.on_event("startup")
def startup_event():
    # 加入這行：無情拆除所有舊表格 (猛藥！)
    #Base.metadata.drop_all(bind=engine) 
    
    # 原本的這行：重新建立符合最新設計圖的表格
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

# --- Pydantic 模型 ---
class UserCreate(BaseModel): username: str; password: str
class RoomCreate(BaseModel): name: str
class JoinRoom(BaseModel): code: str
class MemoryCreate(BaseModel): date: date; content: str
class MemoryUpdate(BaseModel): date: date; content: str
class RoomUpdate(BaseModel): start_date: Optional[date] = None; meetup_count: int

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.username == token).first()
    if not user: raise HTTPException(status_code=401, detail="無效的憑證")
    return user

# --- API 路由 ---
@app.post("/register")
def register(user: UserCreate, db: Session = Depends(get_db)):
    # 【雙重保險】如果表格真的沒建成功，這裡再強制建一次
    Base.metadata.create_all(bind=engine)
    
    if db.query(DBUser).filter(DBUser.username == user.username).first():
        raise HTTPException(status_code=400, detail="帳號已存在")
    
    new_user = DBUser(username=user.username, hashed_password=get_password_hash(user.password))
    db.add(new_user)
    
    room_id = uuid.uuid4().hex
    new_room = DBRoom(id=room_id, name="我的個人隨筆", link_code=generate_code())
    db.add(new_room)
    
    db.add(DBUserRoom(username=user.username, room_id=room_id))
    db.commit()
    return {"msg": "註冊成功！"}

@app.post("/token")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="帳號或密碼錯誤")
    return {"access_token": user.username, "token_type": "bearer"}

@app.get("/rooms")
def get_my_rooms(current_user: DBUser = Depends(get_current_user), db: Session = Depends(get_db)):
    user_rooms = db.query(DBUserRoom).filter(DBUserRoom.username == current_user.username).all()
    room_ids = [ur.room_id for ur in user_rooms]
    rooms = db.query(DBRoom).filter(DBRoom.id.in_(room_ids)).all()
    return rooms

@app.post("/rooms")
def create_room(room: RoomCreate, current_user: DBUser = Depends(get_current_user), db: Session = Depends(get_db)):
    new_room_id = uuid.uuid4().hex
    new_room = DBRoom(id=new_room_id, name=room.name, link_code=generate_code())
    db.add(new_room)
    db.add(DBUserRoom(username=current_user.username, room_id=new_room_id))
    db.commit()
    return {"msg": "房間建立成功"}

@app.post("/join_room")
def join_room(data: JoinRoom, current_user: DBUser = Depends(get_current_user), db: Session = Depends(get_db)):
    target_room = db.query(DBRoom).filter(DBRoom.link_code == data.code).first()
    if not target_room: raise HTTPException(status_code=404, detail="找不到此房間代碼")
    
    exists = db.query(DBUserRoom).filter(DBUserRoom.username == current_user.username, DBUserRoom.room_id == target_room.id).first()
    if exists: raise HTTPException(status_code=400, detail="你已經在這個房間裡囉！")
    
    db.add(DBUserRoom(username=current_user.username, room_id=target_room.id))
    db.commit()
    return {"msg": "成功加入房間！"}

@app.get("/room_info/{room_id}")
def get_room_info(room_id: str, current_user: DBUser = Depends(get_current_user), db: Session = Depends(get_db)):
    room = db.query(DBRoom).filter(DBRoom.id == room_id).first()
    return {"start_date": room.start_date, "meetup_count": room.meetup_count}

@app.put("/room_info/{room_id}")
def update_room_info(room_id: str, info: RoomUpdate, current_user: DBUser = Depends(get_current_user), db: Session = Depends(get_db)):
    room = db.query(DBRoom).filter(DBRoom.id == room_id).first()
    room.start_date = info.start_date
    room.meetup_count = info.meetup_count
    db.commit()
    return {"msg": "更新成功"}

@app.post("/memories/{room_id}")
def create_memory(room_id: str, memory: MemoryCreate, current_user: DBUser = Depends(get_current_user), db: Session = Depends(get_db)):
    new_memory = DBMemory(room_id=room_id, date=memory.date, content=memory.content, author=current_user.username)
    db.add(new_memory)
    db.commit()
    return {"msg": "紀錄已新增"}

@app.get("/memories/{room_id}")
def get_memories(room_id: str, current_user: DBUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(DBMemory).filter(DBMemory.room_id == room_id).order_by(DBMemory.date.desc()).all()

@app.put("/memories/{room_id}/{memory_id}")
def update_memory(room_id: str, memory_id: int, memory: MemoryUpdate, current_user: DBUser = Depends(get_current_user), db: Session = Depends(get_db)):
    db_memory = db.query(DBMemory).filter(DBMemory.id == memory_id, DBMemory.room_id == room_id).first()
    if not db_memory: raise HTTPException(status_code=404, detail="找不到這筆紀錄")
    db_memory.date = memory.date
    db_memory.content = memory.content
    db.commit()
    return {"msg": "紀錄已修改"}

@app.get("/")
def serve_home():
    with open("index.html", "r", encoding="utf-8") as f: return HTMLResponse(content=f.read())
