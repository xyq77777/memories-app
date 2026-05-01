from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime, date
from typing import Optional
import uuid
import random
import string
import hashlib

# --- 資料庫設定 (SQLite) ---
SQLALCHEMY_DATABASE_URL = "sqlite:///./memories.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- 資料庫模型 ---
class DBUser(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    room_id = Column(String, index=True) 
    link_code = Column(String, unique=True, index=True, nullable=True) 

class DBMemory(Base):
    __tablename__ = "memories"
    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(String, index=True) 
    date = Column(Date)
    content = Column(String)
    author = Column(String)

# 【新增】專屬空間資訊模型 (紀錄天數與見面次數)
class DBRoom(Base):
    __tablename__ = "rooms"
    room_id = Column(String, primary_key=True, index=True)
    start_date = Column(Date, nullable=True)
    meetup_count = Column(Integer, default=0)

Base.metadata.create_all(bind=engine)

# --- 密碼加密與驗證 ---
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

def get_password_hash(password):
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(plain_password, hashed_password):
    return get_password_hash(plain_password) == hashed_password

# --- FastAPI 應用程式 ---
app = FastAPI(title="Shared Memory API")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Pydantic 模型 ---
class UserCreate(BaseModel):
    username: str
    password: str

class MemoryCreate(BaseModel):
    date: date
    content: str

class MemoryUpdate(BaseModel):
    date: date
    content: str

class LinkSubmit(BaseModel):
    code: str

# 【新增】用於更新空間資訊的模型
class RoomUpdate(BaseModel):
    start_date: Optional[date] = None
    meetup_count: int

# --- 獲取當前登入使用者 ---
def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.username == token).first()
    if not user:
        raise HTTPException(status_code=401, detail="無效的憑證")
    return user

# --- API 路由 ---
@app.post("/register")
def register(user: UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(DBUser).filter(DBUser.username == user.username).first()
    if db_user:
        raise HTTPException(status_code=400, detail="帳號已存在")
    
    new_room_id = uuid.uuid4().hex 
    new_user = DBUser(
        username=user.username, 
        hashed_password=get_password_hash(user.password),
        room_id=new_room_id
    )
    # 同時初始化這個人的空間儀表板
    new_room = DBRoom(room_id=new_room_id, meetup_count=0)
    
    db.add(new_user)
    db.add(new_room)
    db.commit()
    return {"msg": "註冊成功！"}

@app.post("/token")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="帳號或密碼錯誤")
    return {"access_token": user.username, "token_type": "bearer"}

@app.post("/generate_link")
def generate_link(current_user: DBUser = Depends(get_current_user), db: Session = Depends(get_db)):
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    current_user.link_code = code
    db.commit()
    return {"link_code": code}

@app.post("/join_link")
def join_link(data: LinkSubmit, current_user: DBUser = Depends(get_current_user), db: Session = Depends(get_db)):
    target_user = db.query(DBUser).filter(DBUser.link_code == data.code).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="無效的綁定代碼")
    if target_user.username == current_user.username:
        raise HTTPException(status_code=400, detail="不能輸入自己的代碼哦")
    
    old_room_id = current_user.room_id
    db.query(DBMemory).filter(DBMemory.room_id == old_room_id).update({"room_id": target_user.room_id})
    current_user.room_id = target_user.room_id
    db.commit()
    return {"msg": "綁定成功！"}

# 【新增】取得與更新空間儀表板 API
@app.get("/room_info")
def get_room_info(current_user: DBUser = Depends(get_current_user), db: Session = Depends(get_db)):
    room = db.query(DBRoom).filter(DBRoom.room_id == current_user.room_id).first()
    if not room:
        room = DBRoom(room_id=current_user.room_id, meetup_count=0)
        db.add(room)
        db.commit()
    return {"start_date": room.start_date, "meetup_count": room.meetup_count}

@app.put("/room_info")
def update_room_info(info: RoomUpdate, current_user: DBUser = Depends(get_current_user), db: Session = Depends(get_db)):
    room = db.query(DBRoom).filter(DBRoom.room_id == current_user.room_id).first()
    if not room:
        room = DBRoom(room_id=current_user.room_id)
        db.add(room)
    room.start_date = info.start_date
    room.meetup_count = info.meetup_count
    db.commit()
    return {"msg": "更新成功"}

@app.post("/memories")
def create_memory(memory: MemoryCreate, current_user: DBUser = Depends(get_current_user), db: Session = Depends(get_db)):
    new_memory = DBMemory(room_id=current_user.room_id, date=memory.date, content=memory.content, author=current_user.username)
    db.add(new_memory)
    db.commit()
    return {"msg": "紀錄已新增"}

@app.get("/memories")
def get_memories(current_user: DBUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(DBMemory).filter(DBMemory.room_id == current_user.room_id).order_by(DBMemory.date.desc()).all()

@app.put("/memories/{memory_id}")
def update_memory(memory_id: int, memory: MemoryUpdate, current_user: DBUser = Depends(get_current_user), db: Session = Depends(get_db)):
    db_memory = db.query(DBMemory).filter(DBMemory.id == memory_id, DBMemory.room_id == current_user.room_id).first()
    if not db_memory:
        raise HTTPException(status_code=404, detail="找不到這筆紀錄")
    db_memory.date = memory.date
    db_memory.content = memory.content
    db.commit()
    return {"msg": "紀錄已修改"}

@app.get("/")
def serve_home():
    with open("index.html", "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content)
