from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    user_id = Column(Integer, primary_key=True)
    username = Column(String)
    balance = Column(Float, default=0.0)
    role = Column(String, default='user') # user, dealer, admin
    referred_by = Column(Integer, ForeignKey('users.user_id'), nullable=True)
    # Убираем отсюда vpn_login и vpn_password, так как ключей может быть много
    # Связь с таблицей ключей
    keys = relationship("VPNKey", back_populates="owner", cascade="all, delete-orphan")

class VPNKey(Base):
    __tablename__ = 'vpn_keys'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.user_id'))
    vpn_login = Column(String)
    vpn_password = Column(String)
    tariff = Column(String)
    expiry_date = Column(DateTime)
    warning_sent = Column(Boolean, default=False)
    
    # Связь с владельцем (пользователем)
    owner = relationship("User", back_populates="keys")

class VPNServer(Base):
    __tablename__ = 'servers'
    id = Column(Integer, primary_key=True, autoincrement=True)
    ip = Column(String)
    name = Column(String)
    tariff_type = Column(String) # standard или vip
    ssh_user = Column(String)
    ssh_password = Column(String)

# Настройка движка базы данных
engine = create_async_engine('sqlite+aiosqlite:///vpn_bot.db')
async_session = async_sessionmaker(engine, expire_on_commit=False)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def get_user(user_id, username=None):
    async with async_session() as session:
        user = await session.get(User, user_id)
        if not user:
            user = User(user_id=user_id, username=username)
            session.add(user)
            await session.commit()
            await session.refresh(user)
        return user
